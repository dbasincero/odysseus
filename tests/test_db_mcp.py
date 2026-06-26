"""Tests for the Database MCP server core logic (src/db_mcp.py).

Covers the read-only SQL guard, connection loading, and end-to-end SQLite
behavior (schema inspection, querying, write gating), plus document-to-Note.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db_mcp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    db_mcp.reset_caches()
    yield
    db_mcp.reset_caches()


# --------------------------------------------------------------------------- #
# Read-only SQL guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sql", [
    "SELECT * FROM users",
    "select id from t where name = 'bob'",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "EXPLAIN SELECT 1",
    "PRAGMA integrity_check",
    "SHOW TABLES",
    # A literal that merely contains a write word must not trip the guard.
    "SELECT * FROM logs WHERE action = 'delete' AND note = 'drop it'",
    "SELECT update_count FROM stats",  # column name embeds 'update'
])
def test_read_only_allows_reads(sql):
    ok, reason = db_mcp.is_read_only_sql(sql)
    assert ok, reason


@pytest.mark.parametrize("sql", [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN c int",
    "TRUNCATE t",
    "CREATE TABLE t (id int)",
    "SELECT 1; DROP TABLE t",          # stacked statements
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",  # data-modifying CTE
    "",
    "   ",
])
def test_read_only_rejects_writes(sql):
    ok, _ = db_mcp.is_read_only_sql(sql)
    assert not ok


# --------------------------------------------------------------------------- #
# Connection loading
# --------------------------------------------------------------------------- #
def test_load_connections_from_env(monkeypatch):
    monkeypatch.setenv(db_mcp._ENV_CONNECTIONS, json.dumps({"connections": [
        {"id": "a", "name": "A", "type": "sql", "url": "sqlite://", "allow_write": True},
        {"id": "m", "uri": "mongodb://localhost:27017", "database": "d"},
    ]}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent/none.json")
    conns = db_mcp.load_connections()
    assert set(conns) == {"a", "m"}
    assert conns["a"]["allow_write"] is True
    assert conns["m"]["type"] == "mongodb"  # inferred from mongodb:// uri


def test_load_connections_file_and_env_merge(monkeypatch, tmp_path):
    cfg = tmp_path / "db_connections.json"
    cfg.write_text(json.dumps([
        {"id": "a", "type": "sql", "url": "sqlite://", "allow_write": False},
    ]))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", str(cfg))
    # Env overrides the file entry for the same id.
    monkeypatch.setenv(db_mcp._ENV_CONNECTIONS, json.dumps({"connections": [
        {"id": "a", "type": "sql", "url": "sqlite://", "allow_write": True},
    ]}))
    conns = db_mcp.load_connections()
    assert conns["a"]["allow_write"] is True


def test_unknown_connection_message(monkeypatch):
    monkeypatch.setenv(db_mcp._ENV_CONNECTIONS, "")
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent/none.json")
    out = db_mcp.op_query("nope", "SELECT 1")
    assert "unknown connection" in out.lower()


# --------------------------------------------------------------------------- #
# End-to-end SQLite
# --------------------------------------------------------------------------- #
@pytest.fixture
def sqlite_conn(monkeypatch):
    """A read-only SQLite connection seeded with a `widgets` table."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    from sqlalchemy import create_engine, text
    eng = create_engine(f"sqlite:///{tmp.name}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)"))
        c.execute(text("INSERT INTO widgets (name, qty) VALUES ('a', 1), ('b', 2)"))
    eng.dispose()

    def _configure(allow_write):
        monkeypatch.setenv(db_mcp._ENV_CONNECTIONS, json.dumps({"connections": [
            {"id": "s", "name": "S", "type": "sql",
             "url": f"sqlite:///{tmp.name}", "allow_write": allow_write},
        ]}))
        monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent/none.json")
        db_mcp.reset_caches()
    return _configure


def test_list_tables_and_describe(sqlite_conn):
    sqlite_conn(allow_write=False)
    tables = db_mcp.op_list_tables("s")
    assert "widgets" in tables
    desc = db_mcp.op_describe("s", "widgets")
    assert "qty" in desc and "name" in desc
    assert "PK" in desc  # id is the primary key


def test_query_returns_rows(sqlite_conn):
    sqlite_conn(allow_write=False)
    out = db_mcp.op_query("s", "SELECT name, qty FROM widgets ORDER BY qty")
    assert "a" in out and "b" in out
    assert "row(s)" in out


def test_query_refuses_write_on_read_only(sqlite_conn):
    sqlite_conn(allow_write=False)
    out = db_mcp.op_query("s", "DELETE FROM widgets")
    assert out.lower().startswith("refused")
    # And data is untouched.
    assert "2" in db_mcp.op_query("s", "SELECT count(*) FROM widgets")


def test_execute_refused_when_read_only(sqlite_conn):
    sqlite_conn(allow_write=False)
    out = db_mcp.op_execute("s", "DELETE FROM widgets")
    assert "read-only" in out.lower()


def test_execute_allowed_when_write_enabled(sqlite_conn):
    sqlite_conn(allow_write=True)
    out = db_mcp.op_execute("s", "DELETE FROM widgets WHERE name = 'a'")
    assert out.startswith("OK")
    remaining = db_mcp.op_query("s", "SELECT count(*) AS n FROM widgets")
    assert "1" in remaining


def test_diagnose_sqlite(sqlite_conn):
    sqlite_conn(allow_write=False)
    out = db_mcp.op_diagnose("s")
    assert "Diagnostics" in out
    assert "Integrity check" in out


def test_list_connections_redacts_credentials(monkeypatch):
    monkeypatch.setenv(db_mcp._ENV_CONNECTIONS, json.dumps({"connections": [
        {"id": "pg", "type": "sql",
         "url": "postgresql+psycopg://user:secret@host:5432/db"},
    ]}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent/none.json")
    out = db_mcp.op_list_connections()
    assert "secret" not in out
    assert "***" in out


# --------------------------------------------------------------------------- #
# document -> Note
# --------------------------------------------------------------------------- #
def test_document_creates_note(monkeypatch):
    created = {}

    class _Note:
        def __init__(self, **kw):
            created.update(kw)
            self.id = kw.get("id")
            self.title = kw.get("title")
            self.label = kw.get("label")

    class _Session:
        def add(self, obj):
            created["_added"] = obj
        def commit(self):
            created["_committed"] = True
        def refresh(self, obj):
            pass
        def rollback(self):
            pass
        def close(self):
            created["_closed"] = True

    stub = types.ModuleType("core.database")
    stub.Note = _Note
    stub.SessionLocal = lambda: _Session()
    monkeypatch.setitem(sys.modules, "core.database", stub)

    out = db_mcp.op_document("Outage RCA", "# Root cause\nLock contention.", label="incident")
    assert "Documented as note" in out
    assert created["title"] == "Outage RCA"
    assert created["label"] == "incident"
    assert created["source"] == "db_mcp"
    assert created["_committed"] is True


def test_document_requires_title_and_content():
    assert "title" in db_mcp.op_document("", "x").lower()
    assert "content" in db_mcp.op_document("t", "").lower()
