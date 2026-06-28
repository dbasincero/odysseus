"""Security tests for the Database MCP server (src/db_mcp.py).

Adversarial coverage of the read-only guard, write gating, DB-level read-only
enforcement, and credential redaction.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db_mcp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    db_mcp.reset_caches()
    yield
    db_mcp.reset_caches()


# --------------------------------------------------------------------------- #
# Read-only guard — adversarial
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sql", [
    # File / network / OS / DoS side-effect functions (read-path must refuse).
    "SELECT lo_export(1, '/tmp/pwned')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "SELECT dblink('host=evil', 'SELECT 1')",
    "SELECT pg_sleep(60)",
    "SELECT pg_terminate_backend(123)",
    "SELECT load_file('/etc/passwd')",
    "SELECT sleep(10)",
    "SELECT benchmark(100000000, md5('x'))",
    # SQLite engine/db-state writes via PRAGMA assignment.
    "PRAGMA user_version = 5",
    "PRAGMA journal_mode = WAL",
    # Classic write / DDL / stacked / data-modifying CTE.
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x = 1",
    "DROP TABLE t",
    "SELECT 1; DROP TABLE t",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "ATTACH DATABASE '/tmp/e.db' AS e",
])
def test_dangerous_statements_refused(sql):
    ok, reason = db_mcp.is_read_only_sql(sql)
    assert not ok, f"should refuse: {sql}"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM users",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "PRAGMA integrity_check",
    "PRAGMA table_info(users)",
    # Column/table names that merely embed a banned word must not false-trip.
    "SELECT sleep_count, update_time FROM stats",
    "SELECT * FROM logs WHERE note = 'please delete this'",
])
def test_legitimate_reads_allowed(sql):
    ok, reason = db_mcp.is_read_only_sql(sql)
    assert ok, f"should allow: {sql} ({reason})"


# --------------------------------------------------------------------------- #
# Write gating + DB-level read-only enforcement
# --------------------------------------------------------------------------- #
@pytest.fixture
def sqlite_rw(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    from sqlalchemy import create_engine, text
    eng = create_engine(f"sqlite:///{tmp.name}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)"))
        c.execute(text("INSERT INTO t (v) VALUES (1), (2)"))
    eng.dispose()

    def _cfg(allow_write):
        monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS", json.dumps({"connections": [
            {"id": "c", "type": "sql", "url": f"sqlite:///{tmp.name}",
             "allow_write": allow_write}]}))
        monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent")
        db_mcp.reset_caches()
        return tmp.name
    return _cfg


def test_query_path_cannot_write_even_on_writable_conn(sqlite_rw):
    # allow_write=True, but the READ path must still refuse a write.
    sqlite_rw(allow_write=True)
    out = db_mcp.op_query("c", "UPDATE t SET v = 999")
    assert out.lower().startswith("refused")
    assert "999" not in db_mcp.op_query("c", "SELECT v FROM t")


def test_db_level_read_only_blocks_write(sqlite_rw):
    """Defense in depth: the read transaction sets the engine read-only, so a
    write that somehow reached execute() is rejected by SQLite itself."""
    sqlite_rw(allow_write=True)
    from sqlalchemy import text
    eng = db_mcp._sql_engine(db_mcp.get_connection("c"))
    with eng.connect() as raw:
        trans = raw.begin()
        db_mcp._apply_read_only(raw, eng.dialect.name)
        try:
            with pytest.raises(Exception):
                raw.execute(text("UPDATE t SET v = 0"))
        finally:
            trans.rollback()


def test_execute_refused_on_read_only_conn(sqlite_rw):
    sqlite_rw(allow_write=False)
    out = db_mcp.op_execute("c", "DELETE FROM t")
    assert "read-only" in out.lower()
    assert "2" in db_mcp.op_query("c", "SELECT count(*) FROM t")


# --------------------------------------------------------------------------- #
# Credential redaction
# --------------------------------------------------------------------------- #
def test_redact_masks_userinfo_and_query_string():
    assert db_mcp._redact("postgresql://u:p4ss@host/db") == "postgresql://***@host/db"
    red = db_mcp._redact("mysql://host/db?user=root&password=SECRET")
    assert "SECRET" not in red and "root" not in red


def test_list_connections_never_leaks_password(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS", json.dumps({"connections": [
        {"id": "pg", "type": "sql",
         "url": "postgresql+psycopg://admin:TOPSECRET@db:5432/app"}]}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent")
    assert "TOPSECRET" not in db_mcp.op_list_connections()


def test_safe_err_scrubs_connection_url():
    conn = {"url": "postgresql://u:SECRET@h/db"}
    msg = db_mcp._safe_err(Exception("connect failed postgresql://u:SECRET@h/db"), conn)
    assert "SECRET" not in msg


def test_query_error_does_not_leak_credentials(monkeypatch):
    # Bad host → connection error whose text must not expose the password.
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS", json.dumps({"connections": [
        {"id": "bad", "type": "sql",
         "url": "postgresql+psycopg://u:LEAKME@127.0.0.1:1/db"}]}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent")
    out = db_mcp.op_query("bad", "SELECT 1")
    assert "LEAKME" not in out
