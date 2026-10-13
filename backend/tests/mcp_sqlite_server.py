"""A dependency-free SQLite MCP server for the tests, shaped like the reference sqlite server:
list_tables, describe_table, read_query, write_query - results returned as Python literals."""
import json
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else ":memory:"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
S = {"type": "string"}
TOOLS = [
    {"name": "read_query", "description": "Execute a SELECT query on the SQLite database", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"query": S}, "required": ["query"]}},
    {"name": "write_query", "description": "Execute an INSERT, UPDATE, or DELETE query",
     "inputSchema": {"type": "object", "properties": {"query": S}, "required": ["query"]}},
    {"name": "list_tables", "description": "List all tables", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object", "properties": {}}},
    {"name": "describe_table", "description": "Get the schema of a table", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"table_name": S}, "required": ["table_name"]}},
]


def rows(sql, params=()):
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()] if cur.description else []


def call(name, args):
    if name == "read_query":
        if not args["query"].strip().upper().startswith(("SELECT", "WITH", "PRAGMA")):
            raise ValueError("Only SELECT queries are allowed for read_query")
        return str(rows(args["query"]))
    if name == "write_query":
        cur = conn.execute(args["query"])
        conn.commit()
        return str([{"affected_rows": cur.rowcount}])
    if name == "list_tables":
        return str(rows("SELECT name FROM sqlite_master WHERE type='table'"))
    if name == "describe_table":
        return str(rows(f"PRAGMA table_info({args['table_name']})"))
    raise ValueError(f"unknown tool {name}")


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if rid is None:
            continue
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}}, "serverInfo": {"name": "sqlite", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                result = {"content": [{"type": "text", "text": call(params["name"], params.get("arguments") or {})}]}
            except Exception as e:  # noqa: BLE001
                result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
