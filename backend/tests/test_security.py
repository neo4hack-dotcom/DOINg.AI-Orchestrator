"""Audit fixes: the agents' sandbox (app data, .git, symlinks), MCP project approvals, mod validation without
running code, secret masking, webfetch guard, the privacy report."""
import os

import httpx
import pytest

from backend import netguard, tools
from backend.config import DATA_DIR, mask_server, unmask_server, update_config
from backend.main import app
from backend.mcp_client import project_approval, project_key
from backend.mods import registry
from backend.tools import ToolContext, ToolError


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def ctx_for(ws):
    return ToolContext(directory=ws, session=None, agent="test")


def test_agents_cannot_touch_the_app_data_folder(tmp_path):
    # A project folder that contains the app's data folder (e.g. the app's own repo opened as a project).
    ctx = ToolContext(directory=DATA_DIR.parent, session=None, agent="test")
    (DATA_DIR / "config.json").parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ToolError, match="private data folder"):
        tools.resolve(ctx, str(DATA_DIR / "config.json"), for_read=True)
    with pytest.raises(ToolError, match="private data folder"):
        tools.resolve(ctx, os.path.relpath(DATA_DIR / "sessions", DATA_DIR.parent))


def test_git_internals_are_read_only(workspace):
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("[core]\n")
    ctx = ctx_for(workspace)
    assert tools.resolve(ctx, ".git/config", for_read=True).name == "config"
    with pytest.raises(ToolError, match="inside .git"):
        tools.resolve(ctx, ".git/config")
    with pytest.raises(ToolError, match="inside .git"):
        tools.resolve(ctx, "sub/../.git/hooks/pre-commit")


def test_paths_outside_the_workspace_are_refused(workspace, tmp_path):
    ctx = ctx_for(workspace)
    with pytest.raises(ToolError, match="outside the workspace"):
        tools.resolve(ctx, "../secret.txt", for_read=True)
    with pytest.raises(ToolError, match="outside the workspace"):
        tools.resolve(ctx, str(tmp_path / "elsewhere.txt"))


async def test_grep_skips_symlinks_pointing_outside(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET-TOKEN\n")
    (workspace / "inside.txt").write_text("TOP-SECRET-TOKEN in the project\n")
    try:
        (workspace / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    res = await tools.t_grep(ctx_for(workspace), {"pattern": "TOP-SECRET"})
    assert "inside.txt" in res.output
    assert "link.txt" not in res.output


def test_mcp_project_approval_is_bound_to_the_definition(tmp_path):
    server = {"command": "node", "args": ["server.js"]}
    key = project_key(str(tmp_path), "db", server)
    cfg = {"mcp": {"approvedProjectServers": [key]}}
    assert project_approval(cfg, str(tmp_path), "db", server) == "approved"
    # Any change to the definition (here: a new argument) needs a new approval.
    changed = {"command": "node", "args": ["server.js", "--evil"]}
    assert project_approval(cfg, str(tmp_path), "db", changed) == "changed"
    assert project_approval(cfg, str(tmp_path), "other", server) == "pending"
    # UI-only fields do not invalidate it.
    assert project_approval(cfg, str(tmp_path), "db", {**server, "enabled": False}) == "approved"


def test_mod_validation_never_runs_the_mod(tmp_path):
    folder = tmp_path / "evil"
    folder.mkdir()
    marker = tmp_path / "ran.txt"
    (folder / "mod.json").write_text('{"name": "evil"}')
    (folder / "mod.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\n\ndef register(on, mod):\n    on('tool.call', lambda e, nxt: nxt(e))\n")
    res = registry.validate(folder)
    assert res["ok"], res
    assert res["events"] == ["tool.call"]
    assert not marker.exists()
    (folder / "mod.py").write_text("def register(on, mod):\n    on('not.an.event', None)\n")
    assert not registry.validate(folder)["ok"]


def test_mcp_secrets_are_masked_and_kept():
    srv = {"command": "npx", "args": ["server", "--api-key", "sk-123456", "postgres://bob:hunter2@db:5432/x"],
           "env": {"DB_PASSWORD": "hunter2", "MODE": "ro"}, "url": "https://user:pw@mcp.local/sse?token=abc"}
    shown = mask_server(srv)
    text = repr(shown)
    for secret in ("sk-123456", "hunter2", ":pw@", "token=abc"):
        assert secret not in text, secret
    assert shown["env"]["MODE"] == "ro"
    # The browser sends the masked values back: the stored secrets stay.
    back = unmask_server(shown, srv)
    assert back["args"] == srv["args"] and back["env"]["DB_PASSWORD"] == "hunter2" and back["url"] == srv["url"]


async def test_webfetch_refuses_the_apps_own_api(workspace, monkeypatch):
    monkeypatch.setenv("ORCH_BIND_PORT", "4555")
    with pytest.raises(ToolError, match="own API"):
        await tools.t_webfetch(ctx_for(workspace), {"url": "http://127.0.0.1:4555/api/config"})


def test_local_only_classification():
    assert netguard.url_is_local("http://localhost:11434")
    assert netguard.url_is_local("http://192.168.1.20:8000/v1")
    assert netguard.url_is_local("http://[::1]:8080")
    assert not netguard.url_is_local("http://169.254.169.254/latest/meta-data")  # cloud metadata endpoint
    assert not netguard.url_is_local("https://api.openai.com/v1")


async def test_privacy_report(client, fake_server, workspace):
    def mutate(cfg):
        cfg["llm"].update(provider="openai_compatible", baseUrl=fake_server)
        cfg["network"]["localOnly"] = True
        cfg["mcp"]["servers"] = {"remote": {"url": "https://mcp.example.com/sse", "enabled": True}}
        cfg["permissionMode"] = "ask"
    update_config(mutate)
    try:
        r = (await client.get("/api/privacy")).json()
        assert r["localOnly"] is True
        by_kind = {e["kind"]: e for e in r["endpoints"]}
        assert by_kind["llm"]["local"] is True
        assert by_kind["mcp"]["local"] is False and by_kind["mcp"]["blocked"] is True
        checks = {c["name"]: c["ok"] for c in r["checks"]}
        assert checks["No telemetry"] and checks["Local-only mode"] and checks["Every endpoint is local"]
        assert checks["Confirmation before actions"]
    finally:
        update_config(lambda cfg: cfg["mcp"].update(servers={}))


async def test_local_only_checks_every_redirect():
    update_config(lambda cfg: cfg["network"].update(localOnly=True))
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "127.0.0.1":
            return httpx.Response(302, headers={"location": "https://exfil.example.com/collect"})
        return httpx.Response(200, text="leaked")

    async with netguard.client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        with pytest.raises(netguard.NetworkBlocked):
            await c.get("http://127.0.0.1:9/start")
    assert seen == ["127.0.0.1"]  # the redirect target was never contacted
