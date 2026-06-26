"""
database_server.py

MCP server exposing database access for agents: inspect schema, run read-only
diagnostic queries, optionally apply fixes (per-connection opt-in), and
document findings as Odysseus Notes.

All behavior lives in ``src/db_mcp.py``; this file is a thin stdio wrapper so
the logic stays unit-testable without a subprocess. See that module for the
connection-config format and the read-only security model.
"""

import asyncio
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db_mcp  # noqa: E402

server = Server("database")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="db_list_connections",
            description=(
                "List the configured database connections (id, engine type, and "
                "whether writes are allowed). Always call this first to discover "
                "which databases you can reach."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="db_list_tables",
            description="List tables/views (SQL) or collections (MongoDB) in a connection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection id"},
                },
                "required": ["connection"],
            },
        ),
        Tool(
            name="db_describe",
            description=(
                "Describe a table/view (columns, types, keys, indexes, foreign keys) "
                "or a MongoDB collection (sample field types, indexes)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection id"},
                    "table": {"type": "string", "description": "Table/view or collection name"},
                },
                "required": ["connection", "table"],
            },
        ),
        Tool(
            name="db_query",
            description=(
                "Run a READ-ONLY query. SQL: a single SELECT/WITH/SHOW/EXPLAIN/"
                "DESCRIBE/PRAGMA statement (writes/DDL are refused). MongoDB: a JSON "
                'spec, e.g. {"collection":"users","operation":"find",'
                '"filter":{"age":{"$gt":30}},"limit":20}. Supported Mongo ops: '
                "find, aggregate, count, distinct. Results are capped (default 100 rows)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection id"},
                    "query": {"type": "string", "description": "SQL statement or MongoDB JSON spec"},
                    "limit": {"type": "integer", "description": "Max rows/documents (default 100, max 1000)"},
                },
                "required": ["connection", "query"],
            },
        ),
        Tool(
            name="db_execute",
            description=(
                "Run a WRITE/DDL statement (SQL) or write op (MongoDB insert/update/"
                "delete). Refused unless the connection has allow_write=true. Use this "
                "to apply a fix AFTER diagnosing — and document what you changed with "
                "db_document. MongoDB spec example: "
                '{"collection":"users","operation":"update_one","filter":{"_id":1},'
                '"update":{"$set":{"active":true}}}.'
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection id"},
                    "statement": {"type": "string", "description": "SQL statement or MongoDB JSON write spec"},
                },
                "required": ["connection", "statement"],
            },
        ),
        Tool(
            name="db_diagnose",
            description=(
                "Run a curated set of READ-ONLY health checks for the connection's "
                "engine (sizes, long-running/blocked queries, integrity, connection "
                "counts). A safe starting point for troubleshooting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection id"},
                },
                "required": ["connection"],
            },
        ),
        Tool(
            name="db_document",
            description=(
                "Save findings (diagnosis, root cause, the fix you applied, follow-ups) "
                "as an Odysseus Note so the investigation is documented and searchable "
                "in the UI. Use Markdown in 'content'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title"},
                    "content": {"type": "string", "description": "Note body (Markdown)"},
                    "label": {"type": "string", "description": "Optional label/tag (default 'db')"},
                },
                "required": ["title", "content"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    args = arguments or {}

    def _run() -> str:
        if name == "db_list_connections":
            return db_mcp.op_list_connections()
        if name == "db_list_tables":
            return db_mcp.op_list_tables(args.get("connection", ""))
        if name == "db_describe":
            return db_mcp.op_describe(args.get("connection", ""), args.get("table", ""))
        if name == "db_query":
            return db_mcp.op_query(
                args.get("connection", ""),
                args.get("query", ""),
                args.get("limit", db_mcp.DEFAULT_ROW_LIMIT),
            )
        if name == "db_execute":
            return db_mcp.op_execute(args.get("connection", ""), args.get("statement", ""))
        if name == "db_diagnose":
            return db_mcp.op_diagnose(args.get("connection", ""))
        if name == "db_document":
            return db_mcp.op_document(
                args.get("title", ""),
                args.get("content", ""),
                args.get("label", "db"),
            )
        return f"Unknown tool: {name}"

    try:
        # The db_mcp ops are synchronous (blocking driver I/O); run them off the
        # event loop so a slow query can't stall the MCP stdio transport.
        text = await asyncio.to_thread(_run)
    except Exception as e:  # never let an exception kill the server
        text = f"Error: {type(e).__name__}: {e}"
    return [TextContent(type="text", text=text)]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
