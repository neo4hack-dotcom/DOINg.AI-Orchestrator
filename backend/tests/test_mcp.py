"""MCP client: stdio / Streamable HTTP transports, official SDK interop, agent integration."""
import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

import fake_llm
from backend import mcp_tools
from backend.config import update_config
from backend.main import app
from backend.mcp_client import McpConnection, McpManager, format_result, manager, qualified
from backend.session import store

HERE = Path(__file__).resolve().parent
MINI = str(HERE / "mcp_mini_server.py")
SDK = str(HERE / "mcp_sdk_server.py")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(scope="module")
def mini_http():
    import uvicorn

    import mcp_mini_server

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(mcp_mini_server.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True


async def test_stdio_lifecycle_pagination_and_results(tmp_path):
    conn = McpConnection("mini", {"command": sys.executable, "args": [MINI]}, str(tmp_path))
    await conn.connect()
    try:
        assert conn.status == "connected" and conn.server_info["name"] == "mini"
        assert [t.name for t in conn.tools] == ["add", "write_note", "fail", "pongs", "roots"]  # two pages
        assert conn.instructions.startswith("Mini test server")
        add = conn.tools[0]
        assert add.read_only and add.qualified == "mcp__mini__add"
        assert format_result(await conn.call_tool("add", {"a": 2, "b": 3})) == ("5", False)
        text, err = format_result(await conn.call_tool("fail", {}))
        assert err and text == "boom"
        # the client answered the server's ping (sent during add) and roots/list (sent after initialized)
        text, _ = format_result(await conn.call_tool("pongs", {}))
        assert json.loads(text)["responses"] >= 2
        roots = json.loads(format_result(await conn.call_tool("roots", {}))[0])
        assert roots and roots[0]["uri"].startswith("file:") and tmp_path.name in roots[0]["uri"]
        assert [r["uri"] for r in await conn.list_resources()] == ["note://readme"]
        assert (await conn.read_resource("note://readme"))["contents"][0]["text"] == "hello resource"
        await conn.call_tool("write_note", {"text": "x"})
        await asyncio.sleep(0.2)
        assert any("note 1" in line for line in conn.log)
    finally:
        await conn.close()
    assert conn.status == "stopped"


async def test_disabled_tools_and_qualified_names(tmp_path):
    conn = McpConnection("my server!", {"command": sys.executable, "args": [MINI], "disabledTools": ["fail"]}, str(tmp_path))
    await conn.connect()
    try:
        assert "fail" not in [t.name for t in conn.tools]
        assert conn.tools[0].qualified == "mcp__my_server___add"
        assert len(qualified("s" * 80, "t" * 80)) <= 64
    finally:
        await conn.close()


async def test_manager_reconnects_after_a_crash(tmp_path):
    mgr = McpManager()
    cfg = {"command": sys.executable, "args": [MINI]}
    try:
        assert await mgr.call("mini", cfg, str(tmp_path), "add", {"a": 1, "b": 1}) == ("2", False)
        conn = next(iter(mgr.connections.values()))
        conn.transport.proc.kill()
        await asyncio.sleep(0.3)
        assert await mgr.call("mini", cfg, str(tmp_path), "add", {"a": 2, "b": 2}) == ("4", False)
    finally:
        await mgr.shutdown()


async def test_bad_server_reports_stderr(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nprint('cannot start: missing API key', file=sys.stderr)\nsys.exit(3)\n")
    res = await McpManager().test("bad", {"command": sys.executable, "args": [str(bad)], "startupTimeout": 10}, str(tmp_path))
    assert not res["ok"] and "missing API key" in res["error"]


async def test_streamable_http_with_sse_responses(mini_http, tmp_path):
    import mcp_mini_server

    conn = McpConnection("web", {"type": "http", "url": mini_http, "headers": {"Authorization": "Bearer t0k"}}, str(tmp_path))
    await conn.connect()
    try:
        assert len(conn.tools) == 5
        assert format_result(await conn.call_tool("add", {"a": 20, "b": 22})) == ("42", False)  # SSE response
        assert "Bearer t0k" in mcp_mini_server.STATE["auth"]
        assert "sess-1" in mcp_mini_server.STATE["sessions"]  # session id echoed after initialize
    finally:
        await conn.close()


def test_import_formats_and_schema_cleaning():
    claude = mcp_tools.parse_import(json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "x"], "env": {"K": "v"}}}}))
    assert claude == {"fs": {"command": "npx", "args": ["-y", "x"], "env": {"K": "v"}}}
    vscode = mcp_tools.parse_import(json.dumps({"servers": {"gh": {"type": "streamable-http", "url": "https://x/mcp"}}}))
    assert vscode["gh"]["type"] == "http"
    single = mcp_tools.parse_import(json.dumps({"name": "one", "url": "https://y/sse", "type": "sse"}))
    assert single["one"]["type"] == "sse"
    with pytest.raises(ValueError):
        mcp_tools.parse_import("{}")
    schema = mcp_tools.clean_schema({"$schema": "x", "title": "T", "type": "object",
                                     "properties": {"q": {"type": "string", "description": "d" * 999, "default": "a"}}})
    assert "$schema" not in schema and "title" not in schema and len(schema["properties"]["q"]["description"]) == 300


def test_permission_decisions():
    cfg = {"permissions": {"mcp": "ask"}}
    servers = {"a": {"permission": "ask", "trustReadOnly": True}, "b": {"permission": "deny"}, "c": {"permission": "allow"},
               "d": {"permission": "ask", "trustReadOnly": False}}
    assert mcp_tools.decision(cfg, servers, "a", True, []) == "allow"
    assert mcp_tools.decision(cfg, servers, "a", False, []) == "ask"
    assert mcp_tools.decision(cfg, servers, "a", False, ["mcp:a"]) == "allow"
    assert mcp_tools.decision(cfg, servers, "b", True, ["mcp:b"]) == "deny"
    assert mcp_tools.decision(cfg, servers, "c", False, []) == "allow"
    assert mcp_tools.decision(cfg, servers, "d", True, []) == "ask"
    assert mcp_tools.decision({"permissions": {"mcp": "allow"}}, servers, "d", False, []) == "allow"


def test_rea_server_entry():
    srv = mcp_tools.rea_server({"version": "6.4.0", "provider": "ghidra", "schemaProfile": "compact"})
    assert srv["command"] == "npx" and srv["args"] == ["-y", "rea-agents@6.4.0", "mcp"]
    assert srv["env"]["REA_ANALYSIS_PROVIDER"] == "ghidra" and srv["env"]["REA_MCP_INPUT_SCHEMA_PROFILE"] == "compact"
    cfg = {"mcp": {"servers": {}, "approvedProjectServers": []}, "reverse": {"enabled": True, "acknowledged": True, "version": "6.4.0"}}
    assert "rea" in mcp_tools.servers_for(cfg, ".")
    cfg["reverse"]["acknowledged"] = False
    assert "rea" not in mcp_tools.servers_for(cfg, ".")


# ---------------------------------------------------------------------------
# agent integration (fake LLM)


def _configure(fake_server, workspace, servers, **mcp):
    def mutate(cfg):
        cfg["llm"].update(provider="openai_compatible", baseUrl=fake_server, model="fake-coder:7b", toolMode="auto")
        cfg["workspace"] = str(workspace)
        cfg["autoMode"] = False
        cfg["permissions"].update(edit="allow", bash="allow", mcp="ask")
        cfg["mcp"]["servers"] = servers
        cfg["mcp"]["approvedProjectServers"] = []
        cfg["mcp"]["deferThreshold"] = mcp.get("deferThreshold", 16)
    update_config(mutate)


async def _idle(sid, timeout=20.0):
    for _ in range(int(timeout / 0.05)):
        if not store.get(sid).busy:
            return store.get(sid)
        await asyncio.sleep(0.05)
    raise AssertionError("busy")


def _parts(s):
    return [p for m in s.data["messages"] for p in m["parts"]]


def _scripted(calls_by_turn):
    """A brain that plays the given tool calls turn by turn, then answers with the last tool output."""
    def brain(messages):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        turn = sum(1 for m in messages if m.get("role") == "assistant")
        if turn < len(calls_by_turn):
            return "", calls_by_turn[turn]
        return f"Result: {tool_msgs[-1]['content'] if tool_msgs else ''}", []
    return brain


async def test_build_agent_calls_mcp_tools(client, fake_server, workspace, monkeypatch):
    await manager.shutdown()
    _configure(fake_server, workspace, {"mini": {"command": sys.executable, "args": [MINI], "permission": "ask"}})
    monkeypatch.setattr(fake_llm, "brain", _scripted([[{"name": "mcp__mini__add", "arguments": {"a": 40, "b": 2}}],
                                                      [{"name": "mcp__mini__write_note", "arguments": {"text": "hi"}}]]))
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "use the server", "agent": "build"})
    # add is read-only (trusted); write_note asks for permission
    for _ in range(200):
        pending = store.get(sid).pending_permissions()
        if pending:
            break
        await asyncio.sleep(0.05)
    assert pending and pending[0]["category"] == "mcp" and pending[0]["tool"] == "mcp__mini__write_note"
    await client.post(f"/api/sessions/{sid}/permissions/{pending[0]['id']}", json={"response": "always"})
    s = await _idle(sid)
    tools = [p for p in _parts(s) if p["type"] == "tool"]
    assert [t["status"] for t in tools] == ["done", "done"]
    assert tools[0]["output"] == "42" and tools[0]["metadata"]["mcp"]["server"] == "mini"
    assert "mcp:mini" in s.data["alwaysAllow"]
    assert s.data["mcpCalls"]["mini"] == 2
    await manager.shutdown()


async def test_deferred_mode_search_then_call(client, fake_server, workspace, monkeypatch):
    await manager.shutdown()
    _configure(fake_server, workspace, {"mini": {"command": sys.executable, "args": [MINI]}}, deferThreshold=2)
    seen = {}

    def brain(messages):
        turn = sum(1 for m in messages if m.get("role") == "assistant")
        seen.setdefault("system", str(messages[0]["content"]))
        if turn == 0:
            return "", [{"name": "mcp_search", "arguments": {"query": "add integers"}}]
        if turn == 1:
            seen["search"] = messages[-1]["content"]
            return "", [{"name": "mcp_call", "arguments": {"server": "mini", "tool": "add", "arguments": {"a": 1, "b": 2}}}]
        return f"Sum is {messages[-1]['content']}", []

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "sum", "agent": "build"})
    s = await _idle(sid)
    assert "mcp_search" in seen["system"] and "mini: 5 tool(s)" in seen["system"]
    assert "tool=add" in seen["search"] and '"a"' in seen["search"]
    texts = [p["text"] for p in _parts(s) if p["type"] == "text"]
    assert any("Sum is 3" in t for t in texts)
    await manager.shutdown()


async def test_read_only_agents_only_get_read_only_tools(fake_server, workspace, tmp_path):
    await manager.shutdown()
    _configure(fake_server, workspace, {"mini": {"command": sys.executable, "args": [MINI]}})

    class S:
        directory = str(workspace)
        data: dict = {}

    bundle = await mcp_tools.prepare(S(), True, lambda t: None)
    assert sorted(bundle.tools) == ["mcp__mini__add", "mcp__mini__pongs", "mcp__mini__roots", "mcp_resources"]
    S.data = {"mcpDisabled": ["mini"]}
    assert not (await mcp_tools.prepare(S(), False, lambda t: None)).tools
    await manager.shutdown()


async def test_project_servers_need_approval(client, fake_server, workspace):
    await manager.shutdown()
    _configure(fake_server, workspace, {})
    (workspace / ".mcp.json").write_text(json.dumps({"mcpServers": {"proj": {"command": sys.executable, "args": [MINI]}}}))
    over = (await client.get("/api/mcp", params={"directory": str(workspace)})).json()
    assert over["project"][0]["name"] == "proj" and not over["project"][0]["approved"]

    class S:
        directory = str(workspace)
        data: dict = {}

    notes = []
    assert not (await mcp_tools.prepare(S(), False, notes.append)).tools
    assert notes and "proj" in notes[0]
    r = await client.post("/api/mcp/project/approve", json={"directory": str(workspace), "name": "proj"})
    assert r.status_code == 200
    assert "mcp__proj__add" in (await mcp_tools.prepare(S(), False, notes.append)).tools
    await manager.shutdown()


async def test_mcp_api_crud_masks_secrets(client, fake_server, workspace):
    _configure(fake_server, workspace, {})
    r = await client.put("/api/mcp/servers/gh", json={"type": "http", "url": "https://example.test/mcp",
                                                      "headers": {"Authorization": "Bearer SECRET1"}, "env": {"PATH_HINT": "/x"}})
    assert r.status_code == 200 and r.json()["headers"]["Authorization"] == "********"
    cfg_text = (await client.get("/api/config")).text
    assert "SECRET1" not in cfg_text
    # saving the masked value back keeps the stored secret
    await client.put("/api/mcp/servers/gh", json={"type": "http", "url": "https://example.test/mcp",
                                                  "headers": {"Authorization": "********"}, "description": "GitHub"})
    from backend.config import load_config
    assert load_config()["mcp"]["servers"]["gh"]["headers"]["Authorization"] == "Bearer SECRET1"
    r = await client.post("/api/mcp/import", json={"text": json.dumps({"mcpServers": {"m": {"command": sys.executable, "args": [MINI]}}})})
    assert r.json()["imported"] == ["m"]
    t = (await client.post("/api/mcp/test", json={"name": "m", "directory": str(workspace)})).json()
    assert t["ok"] and len(t["tools"]) == 5
    assert (await client.delete("/api/mcp/servers/gh")).status_code == 200
    names = [s["name"] for s in (await client.get("/api/mcp")).json()["servers"]]
    assert names == ["m"]


# ---------------------------------------------------------------------------
# official SDK interop (skipped when the mcp package is not installed)


def _sdk_available() -> bool:
    try:
        import mcp.types  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _sdk_available(), reason="mcp SDK not installed")
async def test_official_sdk_stdio_interop(tmp_path):
    conn = McpConnection("sdk", {"command": sys.executable, "args": [SDK]}, str(tmp_path))
    await conn.connect()
    try:
        names = {t.name: t for t in conn.tools}
        assert {"multiply", "shout"} <= set(names) and names["multiply"].read_only
        text, err = format_result(await conn.call_tool("multiply", {"a": 6, "b": 7}))
        assert not err and "42" in text
        assert "HELLO" in format_result(await conn.call_tool("shout", {"text": "hello"}))[0]
        res = await conn.read_resource("memo://greeting")
        assert res["contents"][0]["text"] == "hello from the sdk"
        text, err = format_result(await conn.call_tool("multiply", {"a": "x"}))
        assert err  # validation error reported as a tool error
    finally:
        await conn.close()


@pytest.mark.skipif(not _sdk_available(), reason="mcp SDK not installed")
async def test_official_sdk_streamable_http_interop(tmp_path):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, SDK, "http", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            await asyncio.sleep(0.1)
        conn = McpConnection("sdkhttp", {"type": "http", "url": f"http://127.0.0.1:{port}/mcp"}, str(tmp_path))
        await conn.connect()
        try:
            assert "multiply" in [t.name for t in conn.tools]
            assert "12" in format_result(await conn.call_tool("multiply", {"a": 3, "b": 4}))[0]
        finally:
            await conn.close()
    finally:
        proc.kill()
        proc.wait()


async def test_failed_server_is_not_retried_by_every_run(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nsys.exit(1)\n")
    mgr = McpManager()
    cfg = {"command": sys.executable, "args": [str(bad)], "startupTimeout": 10}
    tools, errors = await mgr.tools({"bad": cfg}, str(tmp_path))
    assert not tools and "bad" in errors
    import time as _time
    started = _time.monotonic()
    _, errors = await mgr.tools({"bad": cfg}, str(tmp_path))
    assert "next retry in" in errors["bad"] and _time.monotonic() - started < 1
    await mgr.shutdown()
