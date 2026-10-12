import asyncio
import json

import httpx
import pytest

import fake_llm
from backend import agent
from backend.config import save_config
from backend.main import app
from backend.session import store


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def configure(fake_server, workspace, provider="openai_compatible", **extra):
    agent._text_protocol.clear()
    cfg = {
        "llm": {"provider": provider, "baseUrl": fake_server, "model": "fake-coder:7b", "toolMode": "auto"},
        "workspace": str(workspace),
        "permissions": {"edit": "allow", "bash": "allow"},
        "flow": {"requirePlanApproval": True, "maxParallel": 1, "maxReturnRounds": 2, "gitCommit": False},
    }
    for k, v in extra.items():
        cfg[k] = v
    save_config(cfg)


async def wait_idle(sid, timeout=20.0):
    for _ in range(int(timeout / 0.05)):
        s = store.get(sid)
        if not s.busy:
            return s
        await asyncio.sleep(0.05)
    raise AssertionError("session still busy")


def all_parts(session_data):
    return [p for m in session_data["messages"] for p in m["parts"]]


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
async def test_build_turn_with_native_tools(client, fake_server, workspace, provider):
    configure(fake_server, workspace, provider)
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    r = await client.post(f"/api/sessions/{sid}/message", json={"text": "what is here?", "agent": "build"})
    assert r.status_code == 200
    s = await wait_idle(sid)
    tools = [p for p in all_parts(s.data) if p["type"] == "tool"]
    assert tools and tools[0]["tool"] == "list" and tools[0]["status"] == "done"
    assert "README.md" in tools[0]["output"]
    texts = [p["text"] for p in all_parts(s.data) if p["type"] == "text"]
    assert any("Here is what I found" in t for t in texts)
    assert s.data["title"] == "what is here?"
    assert s.data["tokens"]["input"] > 0


async def test_text_protocol_fallback(client, fake_server, workspace):
    configure(fake_server, workspace, "ollama")
    fake_llm.STATE["no_tools"] = True
    try:
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        await client.post(f"/api/sessions/{sid}/message", json={"text": "look", "agent": "build"})
        s = await wait_idle(sid)
    finally:
        fake_llm.STATE["no_tools"] = False
    parts = all_parts(s.data)
    assert any(p["type"] == "notice" and "text tool protocol" in p["text"] for p in parts)
    tool = next(p for p in parts if p["type"] == "tool")
    assert tool["status"] == "done"
    # the <tool_call> markup is not left in the visible text
    assert not any("<tool_call>" in p.get("text", "") for p in parts if p["type"] == "text")


async def test_permission_ask_then_allow(client, fake_server, workspace):
    configure(fake_server, workspace, permissions={"edit": "allow", "bash": "ask"})
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "run the thing", "agent": "build"})
    pending = []
    for _ in range(200):
        pending = (await client.get(f"/api/sessions/{sid}")).json()["pendingPermissions"]
        if pending:
            break
        await asyncio.sleep(0.05)
    assert pending and pending[0]["tool"] == "bash"
    r = await client.post(f"/api/sessions/{sid}/permissions/{pending[0]['id']}", json={"response": "always"})
    assert r.status_code == 200
    s = await wait_idle(sid)
    tool = next(p for p in all_parts(s.data) if p["type"] == "tool")
    assert tool["status"] == "done" and "hello from bash" in tool["output"]
    assert "printf*" in s.data["alwaysAllow"]


async def test_plan_mode_cannot_edit(client, fake_server, workspace):
    configure(fake_server, workspace)
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    from backend.main import _spec_for
    spec = _spec_for(store.get(sid), "plan")
    assert "write" not in spec.tools and "edit" not in spec.tools
    assert spec.permissions["bash"] == "ask"


async def test_savvy_flow_end_to_end(client, fake_server, workspace):
    configure(fake_server, workspace, "ollama")
    sid = (await client.post("/api/sessions", json={"agent": "flow"})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "/flow add a greeting"})
    s = await wait_idle(sid)
    flow = s.data["flow"]
    assert flow["awaiting"] == "approval" and flow["total"] == 2 and flow["phase"] == "plan"
    assert flow["tasks"][1]["after"] == [1]
    plan_part = next(p for p in all_parts(s.data) if p["type"] == "plan")
    assert plan_part["title"] == "Greeting feature"
    # the raw plan JSON is hidden behind the plan card
    assert any(p.get("hidden") for p in all_parts(s.data) if p["type"] == "text")

    r = await client.post(f"/api/sessions/{sid}/flow/approve")
    assert r.status_code == 200
    s = await wait_idle(sid)
    flow = s.data["flow"]
    assert flow["isFinished"] and flow["phase"] == "close"
    assert flow["done"] == 2 and [t["status"] for t in flow["tasks"]] == ["accepted", "accepted"]
    assert (workspace / "greeting.py").exists() and (workspace / "GREETING.md").exists()
    runs = s.data["runs"]
    assert [r["type"] for r in runs] == ["savvy-light", "savvy-medium"]
    assert all(r["status"] == "done" and r["stepDone"] == 2 and r["stepTotal"] == 2 for r in runs)
    assert all(r["steps"] >= 3 and r["tokens"] > 0 for r in runs)
    reviews = [p for p in all_parts(s.data) if p["type"] == "review"]
    assert [r["verdict"] for r in reviews] == ["accept", "accept"]
    assert any(p["type"] == "text" and "Tiers that ran" in p["text"] for p in all_parts(s.data))


async def test_savvy_flow_return_round_escalates(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace, "openai_compatible", flow={"requirePlanApproval": False, "fastAcceptLight": False})
    original = fake_llm.brain
    seen = {"reviews": 0}

    def brain(messages):
        system = str(messages[0].get("content", ""))
        if "phase REVIEW" in system:
            seen["reviews"] += 1
            if seen["reviews"] == 1:
                return json.dumps({"verdict": "return", "defects": ["greet() must strip the name"], "tier": "careful", "note": "x"}), []
            return json.dumps({"verdict": "accept", "defects": [], "note": "ok"}), []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "add a greeting", "agent": "flow"})
    s = await wait_idle(sid)
    flow = s.data["flow"]
    assert flow["isFinished"] and flow["done"] == 2
    t1 = flow["tasks"][0]
    assert t1["round"] == 1 and t1["tier"] == "careful" and len(t1["runIds"]) == 2
    retry = next(r for r in s.data["runs"] if r["id"] == t1["runIds"][1])
    assert retry["round"] == 1 and retry["type"] == "savvy-careful"
    assert "greet() must strip the name" in retry["messages"][0]["parts"][0]["text"]


async def test_abort_interrupts(client, fake_server, workspace):
    configure(fake_server, workspace, permissions={"edit": "allow", "bash": "ask"})
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "run it", "agent": "build"})
    for _ in range(200):
        if (await client.get(f"/api/sessions/{sid}")).json()["pendingPermissions"]:
            break
        await asyncio.sleep(0.05)
    await client.post(f"/api/sessions/{sid}/abort")
    s = await wait_idle(sid)
    tool = next(p for p in all_parts(s.data) if p["type"] == "tool")
    assert tool["status"] == "error"


async def test_nexus_import(client, tmp_path, fake_server, workspace):
    configure(fake_server, workspace)
    nexus = tmp_path / "NEXUS.AI"
    nexus.mkdir()
    (nexus / "db.json").write_text(json.dumps({"llmConfig": {
        "provider": "openai_compatible", "baseUrl": "http://localhost:1234", "apiKey": "k", "model": "qwen2.5-coder",
        "systemPrompt": "You are NEXUS.AI"}}))
    r = await client.post("/api/config/import-nexus", json={"path": str(nexus)})
    assert r.status_code == 200, r.text
    llm_cfg = r.json()["config"]["llm"]
    assert llm_cfg["provider"] == "openai_compatible" and llm_cfg["model"] == "qwen2.5-coder"
    assert llm_cfg["baseUrl"] == "http://localhost:1234"
    bad = await client.post("/api/config/import-nexus", json={"path": str(tmp_path / "nope")})
    assert bad.status_code == 400


async def test_config_test_lists_models(client, fake_server, workspace):
    configure(fake_server, workspace, "ollama")
    r = (await client.post("/api/config/test", json={})).json()
    assert r["ok"] and "fake-coder:7b" in r["models"]


async def test_file_mentions_are_attached(client, fake_server, workspace):
    configure(fake_server, workspace)
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "explain @README.md", "agent": "build"})
    s = await wait_idle(sid)
    assert '<file path="README.md">' in s.data["history"][0]["content"]
    files = (await client.get(f"/api/sessions/{sid}/files", params={"q": "read"})).json()
    assert files == ["README.md"]
