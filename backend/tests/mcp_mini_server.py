"""A dependency-free MCP server for the tests (stdio or Streamable HTTP via ``app``)."""
from __future__ import annotations

import json
import sys

TOOLS = [
    {"name": "add", "description": "Add two integers.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                     "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}},
    {"name": "write_note", "description": "Store a note (changes server state).",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "pongs", "description": "How many responses the client sent to server requests.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "roots", "description": "The roots the client announced.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {}}},
]
STATE = {"responses": 0, "roots": [], "notes": [], "auth": [], "sessions": []}


def handle(msg: dict, out) -> dict | None:
    """Process one message; ``out(msg)`` sends server-initiated messages. Returns the response (or None)."""
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:  # a response to one of our requests
        STATE["responses"] += 1
        if isinstance(msg.get("result"), dict) and "roots" in msg["result"]:
            STATE["roots"] = msg["result"]["roots"]
        return None
    if method == "notifications/initialized":
        out({"jsonrpc": "2.0", "id": "srv-roots", "method": "roots/list"})
        return None
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {"listChanged": True}, "resources": {}, "logging": {}},
                  "serverInfo": {"name": "mini", "version": "1.0"}, "instructions": "Mini test server: use add for sums."}
    elif method == "tools/list":
        if params.get("cursor") == "p2":
            result = {"tools": TOOLS[2:]}
        else:
            result = {"tools": TOOLS[:2], "nextCursor": "p2"}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "add":
            out({"jsonrpc": "2.0", "id": f"srv-ping-{STATE['responses']}", "method": "ping"})
            result = {"content": [{"type": "text", "text": str(int(args["a"]) + int(args["b"]))}]}
        elif name == "write_note":
            STATE["notes"].append(args.get("text"))
            out({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": f"note {len(STATE['notes'])}"}})
            result = {"content": [{"type": "text", "text": f"stored {len(STATE['notes'])} note(s)"}]}
        elif name == "fail":
            result = {"content": [{"type": "text", "text": "boom"}], "isError": True}
        elif name == "pongs":
            result = {"content": [], "structuredContent": {"responses": STATE["responses"]}}
        elif name == "roots":
            result = {"content": [{"type": "text", "text": json.dumps(STATE["roots"])}]}
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
    elif method == "resources/list":
        result = {"resources": [{"uri": "note://readme", "name": "readme", "mimeType": "text/plain"}]}
    elif method == "resources/read":
        result = {"contents": [{"uri": params.get("uri"), "mimeType": "text/plain", "text": "hello resource"}]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"{method} not found"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main_stdio() -> None:
    def out(m: dict) -> None:
        sys.stdout.write(json.dumps(m) + "\n")
        sys.stdout.flush()

    print("mini server ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        res = handle(json.loads(line), out)
        if res is not None:
            out(res)


# -- Streamable HTTP --------------------------------------------------------
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    app = FastAPI()

    @app.post("/mcp")
    async def mcp_post(req: Request):
        STATE["auth"].append(req.headers.get("authorization"))
        msg = await req.json()
        sid = req.headers.get("mcp-session-id")
        if msg.get("method") != "initialize" and not sid:
            return JSONResponse({"error": "missing session"}, status_code=400)
        if sid:
            STATE["sessions"].append(sid)
        pending: list[dict] = []
        res = handle(msg, pending.append)
        if res is None:
            return Response(status_code=202)
        headers = {"Mcp-Session-Id": "sess-1"} if msg.get("method") == "initialize" else {}
        if msg.get("method") == "tools/call":  # answer as an SSE stream (server messages first)
            async def gen():
                for m in pending + [res]:
                    yield f"event: message\ndata: {json.dumps(m)}\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
        return JSONResponse(res, headers=headers)

    @app.delete("/mcp")
    async def mcp_delete():
        return Response(status_code=200)
except ImportError:  # pragma: no cover
    app = None


if __name__ == "__main__":
    main_stdio()
