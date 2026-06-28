"""
db_mcp.py

Core logic for the built-in Database MCP server
(``mcp_servers/database_server.py``).

Lets agents connect to databases the operator configures, inspect their
schema, run read-only diagnostic queries, optionally apply fixes (per-connection
opt-in), and document what they found as Odysseus Notes.

Supported engines
  - SQL via SQLAlchemy: PostgreSQL, MySQL/MariaDB, SQLite, Oracle (and any other
    dialect SQLAlchemy + an installed driver can reach).
  - MongoDB via pymongo.

Security model (personal-use, self-hosted)
  - Connections come from a config the operator controls: the
    ``ODYSSEUS_DB_CONNECTIONS`` env var (JSON) or a ``db_connections.json`` file
    under ``DATA_DIR`` (which is git-ignored). Drivers/credentials never live in
    the repo.
  - Every connection is READ-ONLY by default. Writes/DDL (SQL) and
    insert/update/delete (Mongo) require an explicit ``"allow_write": true`` on
    that connection.
  - The read path statically rejects anything that isn't a pure read, so an
    over-eager agent cannot mutate a database that was meant for diagnosis only.

The MCP server wrapper is intentionally thin; all behavior lives here so it can
be unit-tested without spawning a subprocess.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default caps so a stray ``SELECT *`` on a huge table can't blow up the agent's
# context window or hang the read.
DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000
QUERY_TIMEOUT_S = 30

_ENV_CONNECTIONS = "ODYSSEUS_DB_CONNECTIONS"
_OWNER_ENV_KEYS = ("ODYSSEUS_MCP_DB_DOC_OWNER", "ODYSSEUS_DB_DOC_OWNER")

# Cache SQLAlchemy engines / Mongo clients per connection id so repeated tool
# calls reuse pools instead of reconnecting each time.
_sql_engines: dict[str, Any] = {}
_mongo_clients: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# Connection config
# --------------------------------------------------------------------------- #
def _data_dir() -> str:
    try:
        from src.constants import DATA_DIR
        return DATA_DIR
    except Exception:
        return os.getenv("ODYSSEUS_DATA_DIR", os.path.join(os.getcwd(), "data"))


def _config_path() -> str:
    override = os.getenv("ODYSSEUS_DB_CONNECTIONS_FILE", "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.join(_data_dir(), "db_connections.json")


def _coerce_connections(raw: Any) -> list[dict]:
    """Accept either a bare list of connections or a ``{"connections": [...]}``
    wrapper, and drop anything that isn't a dict with an id."""
    if isinstance(raw, dict):
        raw = raw.get("connections", [])
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict) and str(item.get("id", "")).strip():
            out.append(item)
    return out


def load_connections() -> dict[str, dict]:
    """Load configured connections keyed by id.

    Merges the env var (``ODYSSEUS_DB_CONNECTIONS``) over the config file so a
    quick env override wins, while the file remains the durable store. Each
    connection is normalized to a known shape.
    """
    merged: dict[str, dict] = {}

    path = _config_path()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for conn in _coerce_connections(json.load(fh)):
                    merged[str(conn["id"]).strip()] = _normalize_connection(conn)
    except (OSError, ValueError) as e:
        logger.warning("Failed to read DB connections file %s: %s", path, e)

    env_raw = os.getenv(_ENV_CONNECTIONS, "").strip()
    if env_raw:
        try:
            for conn in _coerce_connections(json.loads(env_raw)):
                merged[str(conn["id"]).strip()] = _normalize_connection(conn)
        except ValueError as e:
            logger.warning("Failed to parse %s env var: %s", _ENV_CONNECTIONS, e)

    return merged


def _normalize_connection(conn: dict) -> dict:
    conn_type = str(conn.get("type", "")).strip().lower()
    if not conn_type:
        # Infer: a mongo:// URI implies mongodb, otherwise assume SQL.
        uri = str(conn.get("uri") or conn.get("url") or "")
        conn_type = "mongodb" if uri.startswith(("mongodb://", "mongodb+srv://")) else "sql"
    if conn_type in ("mongo", "mongodb"):
        conn_type = "mongodb"
    return {
        "id": str(conn["id"]).strip(),
        "name": str(conn.get("name") or conn["id"]).strip(),
        "type": conn_type,
        "url": conn.get("url") or conn.get("uri"),
        "uri": conn.get("uri") or conn.get("url"),
        "database": conn.get("database"),
        "allow_write": bool(conn.get("allow_write", False)),
    }


def get_connection(conn_id: str) -> Optional[dict]:
    return load_connections().get((conn_id or "").strip())


# --------------------------------------------------------------------------- #
# Read-only SQL guard
# --------------------------------------------------------------------------- #
# A statement is allowed on the read path only if its leading keyword is one of
# these AND it contains no data/schema-mutating keyword anywhere (defends
# against `WITH x AS (...) DELETE ...` and stacked statements).
_READ_FIRST_KEYWORDS = {
    "SELECT", "WITH", "SHOW", "EXPLAIN", "DESCRIBE", "DESC", "PRAGMA", "VALUES",
}
_WRITE_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "GRANT", "REVOKE", "MERGE", "REPLACE", "CALL", "EXEC", "EXECUTE", "COPY",
    "ATTACH", "DETACH", "VACUUM", "REINDEX", "LOCK", "UPSERT", "RENAME",
    "COMMENT", "DO", "SAVEPOINT", "SET", "INTO", "COMMIT", "ROLLBACK",
}
# Functions that are technically SELECT-able but have side effects the read
# path must never allow: writing/reading server files, outbound connections
# (SSRF), running OS commands, loading extensions, or stalling the server (DoS).
# The DB-level read-only transaction (_apply_read_only) blocks the *writing*
# ones too, but this denylist also stops file-reads / SSRF / DoS that a
# read-only transaction permits. Matched as whole tokens (\w+).
_DANGEROUS_FUNCTIONS = {
    # PostgreSQL
    "LO_EXPORT", "LO_IMPORT", "PG_READ_FILE", "PG_READ_BINARY_FILE",
    "PG_LS_DIR", "PG_STAT_FILE", "PG_SLEEP", "PG_SLEEP_FOR", "PG_SLEEP_UNTIL",
    "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND", "PG_RELOAD_CONF",
    "DBLINK", "DBLINK_EXEC", "DBLINK_CONNECT",
    # MySQL
    "LOAD_FILE", "SLEEP", "BENCHMARK", "SYS_EXEC", "SYS_EVAL",
    # SQLite (loadable extensions / fs)
    "LOAD_EXTENSION", "READFILE", "WRITEFILE", "EDIT", "FTS3_TOKENIZER",
}


def _strip_sql(sql: str) -> str:
    """Remove comments and string literals so keyword scanning sees only code.

    Stripping literals stops a value like ``WHERE action = 'delete'`` from
    tripping the write-keyword check.
    """
    # Block comments /* ... */
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Line comments -- ... and # ... (MySQL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"#[^\n]*", " ", sql)
    # Single- and double-quoted string literals (handles doubled-quote escapes)
    sql = re.sub(r"'(?:''|[^'])*'", " '' ", sql)
    sql = re.sub(r'"(?:""|[^"])*"', ' "" ', sql)
    return sql


def is_read_only_sql(sql: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``ok`` is True only for a single pure-read
    statement. Errs on the side of rejection."""
    if not sql or not sql.strip():
        return False, "empty statement"

    stripped = _strip_sql(sql)
    # Reject stacked statements (e.g. "SELECT 1; DROP TABLE t"). Trailing
    # semicolons / whitespace are fine.
    statements = [s for s in stripped.split(";") if s.strip()]
    if len(statements) > 1:
        return False, "multiple statements are not allowed on the read path"

    tokens = re.findall(r"[A-Za-z_]+", stripped.upper())
    if not tokens:
        return False, "no SQL keyword found"
    if tokens[0] not in _READ_FIRST_KEYWORDS:
        return False, (
            f"statement starts with '{tokens[0]}', which is not a read. "
            f"Allowed: {', '.join(sorted(_READ_FIRST_KEYWORDS))}. "
            "Use db_execute on a write-enabled connection instead."
        )
    hit = _WRITE_KEYWORDS.intersection(tokens)
    if hit:
        return False, f"statement contains write/DDL keyword(s): {', '.join(sorted(hit))}"
    danger = _DANGEROUS_FUNCTIONS.intersection(tokens)
    if danger:
        return False, (
            "statement uses a function not allowed on the read path "
            f"(file/network/OS/DoS side effects): {', '.join(sorted(danger))}"
        )
    # A PRAGMA with an assignment writes engine/db state (e.g.
    # `PRAGMA user_version = 5`, `PRAGMA journal_mode = WAL`). Read pragmas
    # (integrity_check, table_info(...)) carry no '=', so this stays precise.
    if tokens[0] == "PRAGMA" and "=" in stripped:
        return False, "PRAGMA assignments are not allowed on the read path"
    return True, ""


# --------------------------------------------------------------------------- #
# SQL engine helpers
# --------------------------------------------------------------------------- #
def _sql_engine(conn: dict):
    """Return a cached SQLAlchemy engine for the connection, with a clear error
    if the driver package is missing."""
    cid = conn["id"]
    if cid in _sql_engines:
        return _sql_engines[cid]
    url = conn.get("url")
    if not url:
        raise ValueError(f"connection '{cid}' has no 'url'")
    try:
        from sqlalchemy import create_engine
    except ImportError as e:  # pragma: no cover - sqlalchemy is a core dep
        raise RuntimeError(f"SQLAlchemy not available: {e}") from e
    try:
        engine = create_engine(url, pool_pre_ping=True)
    except Exception as e:
        raise RuntimeError(_driver_hint(url, e)) from e
    _sql_engines[cid] = engine
    return engine


def _driver_hint(url: str, err: Exception) -> str:
    """Translate a missing-dialect error into an actionable install hint."""
    low = str(url).lower()
    pkg = None
    if low.startswith(("postgres", "postgresql")):
        pkg = "psycopg[binary]"
    elif low.startswith(("mysql", "mariadb")):
        pkg = "PyMySQL  (use a 'mysql+pymysql://' URL)"
    elif low.startswith("oracle"):
        pkg = "oracledb  (use an 'oracle+oracledb://' URL)"
    elif low.startswith("mssql"):
        pkg = "pyodbc"
    if pkg:
        return (
            f"Could not open '{url.split('://', 1)[0]}://...': {err}. "
            f"The driver may be missing — install it with `pip install {pkg}`."
        )
    return f"Could not open database: {err}"


# --------------------------------------------------------------------------- #
# Mongo helpers
# --------------------------------------------------------------------------- #
def _mongo_db(conn: dict):
    cid = conn["id"]
    if cid not in _mongo_clients:
        try:
            from pymongo import MongoClient
        except ImportError as e:
            raise RuntimeError(
                "MongoDB connections require pymongo — install it with "
                "`pip install pymongo`."
            ) from e
        uri = conn.get("uri")
        if not uri:
            raise ValueError(f"connection '{cid}' has no 'uri'")
        _mongo_clients[cid] = MongoClient(uri, serverSelectionTimeoutMS=QUERY_TIMEOUT_S * 1000)
    client = _mongo_clients[cid]
    db_name = conn.get("database")
    if not db_name:
        # Fall back to the database encoded in the URI, if any.
        try:
            default = client.get_default_database()
            if default is not None:
                return default
        except Exception:
            pass
        raise ValueError(
            f"connection '{cid}' needs a 'database' (none set and the URI has no default)"
        )
    return client[db_name]


# --------------------------------------------------------------------------- #
# Output formatting
# --------------------------------------------------------------------------- #
def _fmt_table(columns: list[str], rows: list[tuple]) -> str:
    """Render rows as a compact Markdown table."""
    if not columns:
        return "(no columns)"
    widths = [len(c) for c in columns]
    str_rows = []
    for row in rows:
        cells = ["" if v is None else str(v) for v in row]
        cells = [c.replace("\n", " ⏎ ") for c in cells]
        for i, c in enumerate(cells):
            if i < len(widths):
                widths[i] = min(max(widths[i], len(c)), 60)
        str_rows.append(cells)

    def _fmt_row(cells):
        out = []
        for i, c in enumerate(cells):
            w = widths[i] if i < len(widths) else len(c)
            c = c if len(c) <= w else c[: w - 1] + "…"
            out.append(c.ljust(w))
        return "| " + " | ".join(out) + " |"

    header = _fmt_row(columns)
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    body = "\n".join(_fmt_row(r) for r in str_rows)
    return "\n".join([header, sep, body]) if body else "\n".join([header, sep])


def _json_default(o):
    return str(o)


# --------------------------------------------------------------------------- #
# Public operations (return Markdown strings)
# --------------------------------------------------------------------------- #
def op_list_connections() -> str:
    conns = load_connections()
    if not conns:
        return (
            "No database connections configured.\n\n"
            f"Add them to `{_config_path()}` or the `{_ENV_CONNECTIONS}` env var. "
            "Example:\n\n```json\n"
            '{\n  "connections": [\n'
            '    {"id": "appdb", "name": "App DB", "type": "sql",\n'
            '     "url": "postgresql+psycopg://user:pass@localhost:5432/app",\n'
            '     "allow_write": false}\n  ]\n}\n```'
        )
    lines = [f"**{len(conns)} database connection(s):**", ""]
    for c in conns.values():
        mode = "read+write" if c["allow_write"] else "read-only"
        target = c.get("url") or c.get("uri") or ""
        lines.append(
            f"- `{c['id']}` — {c['name']} ({c['type']}, **{mode}**)"
            + (f"\n    {_redact(target)}" if target else "")
        )
    return "\n".join(lines)


def _redact(target: str) -> str:
    """Hide credentials in a connection string before showing it.

    Masks both ``scheme://user:pass@host`` and credentials carried in the query
    string (``?password=...`` / ``user=...``), which the userinfo form misses.
    """
    s = re.sub(r"://[^@/]*@", "://***@", str(target))
    s = re.sub(r"(?i)((?:password|passwd|pwd|user|username|uid)=)[^&\s;]+", r"\1***", s)
    return s


def _safe_err(e, conn: dict | None = None) -> str:
    """Render an exception for the user with any connection credentials masked —
    SQLAlchemy/driver errors can echo the DSN (with password) back."""
    msg = _redact(str(e))
    if conn:
        for key in ("url", "uri"):
            raw = conn.get(key)
            if raw:
                msg = msg.replace(str(raw), _redact(str(raw)))
    return msg


def op_list_tables(conn_id: str) -> str:
    conn = get_connection(conn_id)
    if not conn:
        return _unknown_conn(conn_id)
    try:
        if conn["type"] == "mongodb":
            db = _mongo_db(conn)
            names = sorted(db.list_collection_names())
            if not names:
                return f"No collections found in `{conn_id}`."
            return f"**Collections in `{conn_id}` ({len(names)}):**\n" + "\n".join(
                f"- {n}" for n in names
            )
        from sqlalchemy import inspect as sa_inspect
        engine = _sql_engine(conn)
        insp = sa_inspect(engine)
        tables = sorted(insp.get_table_names())
        views = sorted(insp.get_view_names())
        lines = [f"**Tables in `{conn_id}` ({len(tables)}):**"]
        lines += [f"- {t}" for t in tables] or ["(none)"]
        if views:
            lines.append(f"\n**Views ({len(views)}):**")
            lines += [f"- {v}" for v in views]
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing tables for `{conn_id}`: {_safe_err(e, conn)}"


def op_describe(conn_id: str, table: str) -> str:
    conn = get_connection(conn_id)
    if not conn:
        return _unknown_conn(conn_id)
    if not table or not table.strip():
        return "Error: describe needs a table/collection name."
    table = table.strip()
    try:
        if conn["type"] == "mongodb":
            return _describe_mongo(conn, table)
        return _describe_sql(conn, table)
    except Exception as e:
        return f"Error describing `{table}` on `{conn_id}`: {_safe_err(e, conn)}"


def _describe_sql(conn: dict, table: str) -> str:
    from sqlalchemy import inspect as sa_inspect
    engine = _sql_engine(conn)
    insp = sa_inspect(engine)
    if table not in insp.get_table_names() and table not in insp.get_view_names():
        return f"Table/view `{table}` not found on `{conn['id']}`."
    cols = insp.get_columns(table)
    rows = []
    pks = set(insp.get_pk_constraint(table).get("constrained_columns") or [])
    for c in cols:
        rows.append((
            c["name"],
            str(c.get("type", "")),
            "NO" if not c.get("nullable", True) else "YES",
            "PK" if c["name"] in pks else "",
            "" if c.get("default") is None else str(c.get("default")),
        ))
    out = [f"**`{table}` columns:**", _fmt_table(
        ["column", "type", "nullable", "key", "default"], rows)]
    try:
        idx = insp.get_indexes(table)
        if idx:
            out.append("\n**Indexes:**")
            for i in idx:
                uniq = " (unique)" if i.get("unique") else ""
                out.append(f"- {i.get('name')}: {', '.join(i.get('column_names') or [])}{uniq}")
    except Exception:
        pass
    try:
        fks = insp.get_foreign_keys(table)
        if fks:
            out.append("\n**Foreign keys:**")
            for fk in fks:
                out.append(
                    f"- {', '.join(fk.get('constrained_columns') or [])} → "
                    f"{fk.get('referred_table')}({', '.join(fk.get('referred_columns') or [])})"
                )
    except Exception:
        pass
    return "\n".join(out)


def _describe_mongo(conn: dict, collection: str) -> str:
    db = _mongo_db(conn)
    coll = db[collection]
    count = coll.estimated_document_count()
    sample = coll.find_one()
    lines = [f"**Collection `{collection}`** (~{count} documents)"]
    if sample:
        lines.append("\nSample document field types:")
        for k, v in sample.items():
            lines.append(f"- {k}: {type(v).__name__}")
    else:
        lines.append("\n(collection is empty)")
    try:
        idx_info = coll.index_information()
        if idx_info:
            lines.append("\n**Indexes:**")
            for name, info in idx_info.items():
                keys = ", ".join(f"{k}:{d}" for k, d in info.get("key", []))
                lines.append(f"- {name}: {keys}")
    except Exception:
        pass
    return "\n".join(lines)


def op_query(conn_id: str, query: str, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Run a READ-only query. SQL: a single SELECT/WITH/SHOW/... statement.
    Mongo: a JSON spec ``{"collection", "operation": find|aggregate|count|
    distinct, ...}``."""
    conn = get_connection(conn_id)
    if not conn:
        return _unknown_conn(conn_id)
    try:
        limit = max(1, min(int(limit or DEFAULT_ROW_LIMIT), MAX_ROW_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_ROW_LIMIT
    try:
        if conn["type"] == "mongodb":
            return _query_mongo(conn, query, limit, write=False)
        return _query_sql(conn, query, limit)
    except Exception as e:
        return f"Query error on `{conn_id}`: {_safe_err(e, conn)}"


def _apply_read_only(conn_exec, dialect: str) -> None:
    """Best-effort DB-level read-only enforcement for the current transaction —
    a second line of defense behind is_read_only_sql() so even a SELECT with a
    writing side effect (e.g. lo_export) is rejected by the server itself."""
    from sqlalchemy import text
    try:
        if dialect in ("postgresql", "mysql", "mariadb"):
            conn_exec.execute(text("SET TRANSACTION READ ONLY"))
        elif dialect == "sqlite":
            conn_exec.execute(text("PRAGMA query_only = ON"))
    except Exception:
        # Dialect doesn't support it / already in a txn — the keyword + function
        # guards still apply, so fail open on the hardening, not on safety.
        pass


def _query_sql(conn: dict, sql: str, limit: int) -> str:
    ok, reason = is_read_only_sql(sql)
    if not ok:
        return f"Refused: {reason}"
    from sqlalchemy import text
    engine = _sql_engine(conn)
    # Run inside a transaction we always roll back, so even a read that touches
    # a writable connection leaves no trace.
    with engine.connect() as raw:
        conn_exec = raw.execution_options(no_parameters=True)
        trans = conn_exec.begin()
        try:
            _apply_read_only(conn_exec, engine.dialect.name)
            result = conn_exec.execute(text(sql))
            if not result.returns_rows:
                return "(statement executed; no rows returned)"
            columns = list(result.keys())
            rows = result.fetchmany(limit)
            extra = result.fetchone() is not None
        finally:
            trans.rollback()
    body = _fmt_table(columns, rows)
    note = f"\n\n_Showing {len(rows)} row(s)" + (f"; more available (limit {limit})._" if extra else "._")
    return body + note


def _query_mongo(conn: dict, spec_raw: str, limit: int, write: bool) -> str:
    try:
        spec = json.loads(spec_raw) if isinstance(spec_raw, str) else spec_raw
    except ValueError as e:
        return f"Refused: MongoDB query must be a JSON spec object: {e}"
    if not isinstance(spec, dict):
        return "Refused: MongoDB query spec must be a JSON object."
    collection = spec.get("collection")
    operation = str(spec.get("operation", "find")).lower()
    if not collection:
        return "Refused: spec needs a 'collection'."
    read_ops = {"find", "aggregate", "count", "count_documents", "distinct"}
    if not write and operation not in read_ops:
        return (
            f"Refused: '{operation}' is a write operation. "
            "Enable allow_write on this connection and use db_execute."
        )
    db = _mongo_db(conn)
    coll = db[collection]

    if operation == "find":
        cursor = coll.find(spec.get("filter") or {}, spec.get("projection"))
        if spec.get("sort"):
            cursor = cursor.sort(list(spec["sort"].items()))
        docs = list(cursor.limit(limit))
        return _fmt_mongo_docs(docs, limit)
    if operation == "aggregate":
        pipeline = spec.get("pipeline") or []
        if not write and any(
            isinstance(st, dict) and ("$out" in st or "$merge" in st) for st in pipeline
        ):
            return "Refused: $out/$merge write to the database; not allowed on the read path."
        docs = list(coll.aggregate(pipeline))[:limit]
        return _fmt_mongo_docs(docs, limit)
    if operation in ("count", "count_documents"):
        n = coll.count_documents(spec.get("filter") or {})
        return f"count: {n}"
    if operation == "distinct":
        field = spec.get("field")
        if not field:
            return "Refused: distinct needs a 'field'."
        vals = coll.distinct(field, spec.get("filter") or {})
        return f"distinct {field} ({len(vals)}): " + json.dumps(vals[:limit], default=_json_default)
    return f"Unsupported read operation: {operation}"


def _fmt_mongo_docs(docs: list, limit: int) -> str:
    if not docs:
        return "(no documents matched)"
    shown = docs[:limit]
    text = json.dumps(shown, indent=2, default=_json_default)
    note = f"\n\n_Showing {len(shown)} document(s)._"
    return f"```json\n{text}\n```" + note


def op_execute(conn_id: str, statement: str) -> str:
    """Run a WRITE/DDL statement (SQL) or write op (Mongo). Only permitted on a
    connection with ``allow_write: true``."""
    conn = get_connection(conn_id)
    if not conn:
        return _unknown_conn(conn_id)
    if not conn["allow_write"]:
        return (
            f"Refused: connection `{conn_id}` is read-only. "
            'Set "allow_write": true on it to permit writes.'
        )
    try:
        if conn["type"] == "mongodb":
            return _execute_mongo(conn, statement)
        return _execute_sql(conn, statement)
    except Exception as e:
        return f"Execute error on `{conn_id}`: {_safe_err(e, conn)}"


def _execute_sql(conn: dict, sql: str) -> str:
    if not sql or not sql.strip():
        return "Error: empty statement."
    from sqlalchemy import text
    engine = _sql_engine(conn)
    with engine.begin() as raw:  # commits on success, rolls back on error
        result = raw.execute(text(sql))
        rowcount = result.rowcount if result.rowcount is not None else -1
    if rowcount >= 0:
        return f"OK — {rowcount} row(s) affected."
    return "OK — statement executed."


def _execute_mongo(conn: dict, spec_raw: str) -> str:
    try:
        spec = json.loads(spec_raw) if isinstance(spec_raw, str) else spec_raw
    except ValueError as e:
        return f"Error: write spec must be JSON: {e}"
    if not isinstance(spec, dict):
        return "Error: write spec must be a JSON object."
    collection = spec.get("collection")
    operation = str(spec.get("operation", "")).lower()
    if not collection or not operation:
        return "Error: spec needs 'collection' and 'operation'."
    db = _mongo_db(conn)
    coll = db[collection]
    if operation == "insert_one":
        r = coll.insert_one(spec["document"])
        return f"OK — inserted _id={r.inserted_id}"
    if operation == "insert_many":
        r = coll.insert_many(spec["documents"])
        return f"OK — inserted {len(r.inserted_ids)} document(s)"
    if operation in ("update_one", "update_many"):
        fn = coll.update_one if operation == "update_one" else coll.update_many
        r = fn(spec.get("filter") or {}, spec["update"], upsert=bool(spec.get("upsert")))
        return f"OK — matched {r.matched_count}, modified {r.modified_count}"
    if operation in ("delete_one", "delete_many"):
        fn = coll.delete_one if operation == "delete_one" else coll.delete_many
        r = fn(spec.get("filter") or {})
        return f"OK — deleted {r.deleted_count} document(s)"
    return f"Unsupported write operation: {operation}"


# Curated, read-only diagnostic queries per SQL dialect. Keyed by the dialect
# name SQLAlchemy reports (engine.dialect.name).
_DIAGNOSTICS: dict[str, list[tuple[str, str]]] = {
    "postgresql": [
        ("Database size", "SELECT pg_size_pretty(pg_database_size(current_database())) AS size"),
        ("Active queries > 30s",
         "SELECT pid, state, now()-query_start AS runtime, left(query,80) AS query "
         "FROM pg_stat_activity WHERE state <> 'idle' AND now()-query_start > interval '30 seconds' "
         "ORDER BY runtime DESC LIMIT 20"),
        ("Blocked locks",
         "SELECT count(*) AS waiting_locks FROM pg_locks WHERE NOT granted"),
        ("Top tables by size",
         "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS size "
         "FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10"),
    ],
    "mysql": [
        ("Version", "SELECT VERSION() AS version"),
        ("Open connections", "SHOW STATUS LIKE 'Threads_connected'"),
        ("Slow queries", "SHOW STATUS LIKE 'Slow_queries'"),
        ("Largest tables",
         "SELECT table_name, ROUND((data_length+index_length)/1024/1024,1) AS mb "
         "FROM information_schema.tables WHERE table_schema=DATABASE() "
         "ORDER BY (data_length+index_length) DESC LIMIT 10"),
    ],
    "sqlite": [
        ("Integrity check", "PRAGMA integrity_check"),
        ("Page count", "PRAGMA page_count"),
        ("Foreign-key violations", "PRAGMA foreign_key_check"),
    ],
    "oracle": [
        ("Version", "SELECT banner FROM v$version"),
        ("Tablespace usage",
         "SELECT tablespace_name, ROUND(SUM(bytes)/1024/1024,1) AS mb "
         "FROM dba_segments GROUP BY tablespace_name ORDER BY 2 DESC FETCH FIRST 10 ROWS ONLY"),
    ],
}


def op_diagnose(conn_id: str) -> str:
    """Run a curated set of read-only health checks for the connection's
    engine. A starting point for 'tratar problemas' — safe to run anywhere."""
    conn = get_connection(conn_id)
    if not conn:
        return _unknown_conn(conn_id)
    try:
        if conn["type"] == "mongodb":
            return _diagnose_mongo(conn)
        return _diagnose_sql(conn)
    except Exception as e:
        return f"Diagnostics error on `{conn_id}`: {_safe_err(e, conn)}"


def _diagnose_sql(conn: dict) -> str:
    from sqlalchemy import text
    engine = _sql_engine(conn)
    dialect = engine.dialect.name
    checks = _DIAGNOSTICS.get(dialect)
    out = [f"**Diagnostics for `{conn['id']}` ({dialect})**"]
    if not checks:
        out.append(f"\n_No curated diagnostics for dialect '{dialect}'. "
                   "Use db_query with engine-specific checks._")
        return "\n".join(out)
    with engine.connect() as raw:
        _apply_read_only(raw, dialect)
        for label, sql in checks:
            out.append(f"\n### {label}")
            try:
                result = raw.execute(text(sql))
                if result.returns_rows:
                    cols = list(result.keys())
                    rows = result.fetchmany(20)
                    out.append(_fmt_table(cols, rows) if rows else "_(no rows)_")
                else:
                    out.append("_(ok)_")
            except Exception as e:
                out.append(f"_check failed: {e}_")
            finally:
                try:
                    raw.rollback()
                except Exception:
                    pass
    return "\n".join(out)


def _diagnose_mongo(conn: dict) -> str:
    db = _mongo_db(conn)
    out = [f"**Diagnostics for `{conn['id']}` (mongodb)**"]
    try:
        stats = db.command("dbStats")
        out.append(f"\n- Collections: {stats.get('collections')}")
        out.append(f"- Objects: {stats.get('objects')}")
        data_mb = (stats.get("dataSize") or 0) / 1024 / 1024
        out.append(f"- Data size: {data_mb:.1f} MB")
        out.append(f"- Indexes: {stats.get('indexes')}")
    except Exception as e:
        out.append(f"\n_dbStats failed: {e}_")
    try:
        server_status = db.command("serverStatus")
        conns = server_status.get("connections", {})
        out.append(f"- Current connections: {conns.get('current')}")
        out.append(f"- Uptime: {server_status.get('uptime')} s")
    except Exception:
        pass
    return "\n".join(out)


def op_document(title: str, content: str, label: str = "db") -> str:
    """Persist findings as an Odysseus Note so 'document everything' is durable
    and searchable in the UI."""
    if not title or not title.strip():
        return "Error: document needs a title."
    if not content or not content.strip():
        return "Error: document needs content."
    try:
        import uuid as _uuid

        from core.database import Note, SessionLocal
    except Exception as e:
        return f"Error: Notes storage not available: {e}"

    owner = None
    for key in _OWNER_ENV_KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            owner = val
            break

    db = SessionLocal()
    try:
        note = Note(
            id=str(_uuid.uuid4()),
            owner=owner,
            title=title.strip()[:200],
            content=content,
            note_type="note",
            label=(label or "db").strip()[:50],
            source="db_mcp",
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return f"Documented as note `{note.id}` (label: {note.label}): {note.title}"
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return f"Error saving note: {e}"
    finally:
        db.close()


def _unknown_conn(conn_id: str) -> str:
    known = ", ".join(sorted(load_connections().keys())) or "(none configured)"
    return f"Error: unknown connection `{conn_id}`. Configured: {known}."


def reset_caches() -> None:
    """Drop cached engines/clients. Used by tests and after config changes."""
    for eng in _sql_engines.values():
        try:
            eng.dispose()
        except Exception:
            pass
    _sql_engines.clear()
    for cli in _mongo_clients.values():
        try:
            cli.close()
        except Exception:
            pass
    _mongo_clients.clear()
