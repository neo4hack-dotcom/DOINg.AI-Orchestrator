"""Integration tests for long unattended runs: modular planning, recovery, traceability."""
import asyncio
import json
import shutil

import httpx
import pytest

import fake_llm
from backend import agent, journal
from backend.config import save_config
from backend.main import app, resume_interrupted_flows
from backend.session import store


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_fake():
    yield
    fake_llm.STATE.update(plan_mode="simple", fail_next=0, overflow_next=0, no_tools=False)


def configure(fake_server, workspace, provider="ollama", flow=None, **extra):
    agent._text_protocol.clear()
    cfg = {
        "llm": {"provider": provider, "baseUrl": fake_server, "model": "fake-coder:7b", "toolMode": "auto"},
        "workspace": str(workspace),
        "permissions": {"edit": "allow", "bash": "allow", "autonomous": False},
        "flow": {"requirePlanApproval": False, "maxParallel": 2, "maxReturnRounds": 2, "gitCommit": False,
                 "fastAcceptLight": True, "tokenBudget": 0, "autoVerify": True, **(flow or {})},
        "agent": {"maxRetries": 4, "retryBaseSeconds": 0.01, "doomLoopThreshold": 3},
        "verify": {"commands": [], "timeout": 60},
    }
    cfg.update(extra)
    save_config(cfg)


async def wait_idle(sid, timeout=30.0):
    for _ in range(int(timeout / 0.05)):
        if not store.get(sid).busy:
            return store.get(sid)
        await asyncio.sleep(0.05)
    raise AssertionError("session still busy")


def parts(data):
    return [p for m in data["messages"] for p in m["parts"]]


async def new_session(client, **kw):
    return (await client.post("/api/sessions", json=kw)).json()["id"]


async def test_modular_flow_with_gates_and_integration(client, fake_server, workspace):
    configure(fake_server, workspace)
    fake_llm.STATE["plan_mode"] = "modular"
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "build the greeting app", "agent": "flow"})
    s = await wait_idle(sid)
    flow = s.data["flow"]
    assert flow["mode"] == "modular" and [m["id"] for m in flow["modules"]] == ["core", "docs"]
    assert flow["isFinished"] and flow["done"] == 2, flow
    t1, t2 = flow["tasks"]
    assert t1["module"] == "core" and t2["module"] == "docs" and t2["after"] == [1]
    assert t1["wave"] == 1 and t2["wave"] == 2
    assert t1["checks"][-1]["exitCode"] == 0
    assert flow["modules"][0]["gate"] == 0                      # module gate ran
    assert flow["integration"][0]["exitCode"] == 0              # project-wide check ran
    assert "Contracts this module exposes" in s.run(t1["runIds"][0])["messages"][0]["parts"][0]["text"]
    reviews = [p for p in parts(s.data) if p["type"] == "review"]
    assert reviews[0].get("auto") is True                       # light + passing check: no LLM review
    assert (workspace / "docs" / "PLAN.md").exists() and (workspace / "docs" / "FLOW_REPORT.md").exists()
    kinds = {e["kind"] for e in journal.read(sid)}
    assert {"plan", "task", "verdict", "verify", "phase", "flow_end", "tool"} <= kinds
    report = (await client.get(f"/api/sessions/{sid}/report")).text
    assert "## Tasks" in report and "Create greeting module" in report and "## Timeline" in report
    assert flow["tokens"] > 0
    # worker transcripts are served per run, not with the session
    full = (await client.get(f"/api/sessions/{sid}")).json()
    assert "messages" not in full["runs"][0]
    run = (await client.get(f"/api/sessions/{sid}/runs/{full['runs'][0]['id']}")).json()
    assert run["messages"][0]["role"] == "user"


async def test_transient_errors_are_retried(client, fake_server, workspace):
    configure(fake_server, workspace)
    fake_llm.STATE["fail_next"] = 2
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "what is here?", "agent": "build"})
    s = await wait_idle(sid)
    assert any("Here is what I found" in p.get("text", "") for p in parts(s.data) if p["type"] == "text")
    assert [e["attempt"] for e in journal.read(sid) if e["kind"] == "retry"] == [1, 2]


async def test_context_overflow_recovers(client, fake_server, workspace):
    configure(fake_server, workspace)
    fake_llm.STATE["overflow_next"] = 1
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "what is here?", "agent": "build"})
    s = await wait_idle(sid)
    assert any("Here is what I found" in p.get("text", "") for p in parts(s.data) if p["type"] == "text")


async def test_auto_fix_loop_before_review(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace)
    original = fake_llm.brain

    def brain(messages):
        system = str(messages[0].get("content", ""))
        users = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
        if "worker in the savvy-flow pipeline" in system and "Create greeting module" in users[0]:
            if any("Automatic verification failed" in u for u in users):
                if messages[-1].get("role") == "user":
                    return "", [{"name": "read", "arguments": {"path": "greeting.py"}},
                                {"name": "write", "arguments": {"path": "greeting.py", "content": fake_llm.GREETING_PY}}]
                return "fixed: greeting.py now returns the right text", []
            if fake_llm._assistant_turns(messages) == 0:
                return "", [{"name": "write", "arguments": {"path": "greeting.py", "content": "def greet(name):\n    return 'Hi'\n"}}]
            return "done", []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "greeting", "agent": "flow"})
    s = await wait_idle(sid)
    t1 = s.data["flow"]["tasks"][0]
    assert [c["exitCode"] for c in t1["checks"]] == [1, 0]
    assert t1["status"] == "accepted" and t1["round"] == 0


async def test_token_budget_stops_delegation(client, fake_server, workspace):
    configure(fake_server, workspace, flow={"tokenBudget": 10})
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "greeting", "agent": "flow"})
    s = await wait_idle(sid)
    flow = s.data["flow"]
    assert flow["budgetExceeded"] and flow["isFinished"]
    assert all(t["status"] == "planned" for t in flow["tasks"]) and not s.data["runs"]


async def test_resume_after_restart(client, fake_server, workspace):
    configure(fake_server, workspace, flow={"requirePlanApproval": True})
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "greeting", "agent": "flow"})
    s = await wait_idle(sid)
    assert s.data["flow"]["awaiting"] == "approval"
    # Simulate a crash in the middle of task 1.
    flow = s.data["flow"]
    flow.update(awaiting=None, wasExecuting=True, interrupted=True)
    flow["tasks"][0].update(status="planned", interruptedAttempt=True)
    s.flow_engine = None
    assert resume_interrupted_flows() == [sid]
    s = await wait_idle(sid)
    assert s.data["flow"]["isFinished"] and s.data["flow"]["done"] == 2
    assert s.data["flow"]["resumeCount"] == 1
    brief = s.run(s.data["flow"]["tasks"][0]["runIds"][0])["messages"][0]["parts"][0]["text"]
    assert "previous attempt was interrupted" in brief


@pytest.mark.skipif(not shutil.which("git"), reason="git missing")
async def test_undo_changes_and_fork(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace)
    original = fake_llm.brain

    def brain(messages):
        system = str(messages[0].get("content", ""))
        if "BUILD mode" in system:
            if messages[-1].get("role") == "user":
                return "Writing.", [{"name": "write", "arguments": {"path": "made.txt", "content": "hello\n"}}]
            return "Created made.txt", []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "make a file", "agent": "build"})
    await wait_idle(sid)
    assert (workspace / "made.txt").exists()
    changes = (await client.get(f"/api/sessions/{sid}/changes")).json()["files"]
    assert changes == [{"path": "made.txt", "additions": 1, "deletions": 0, "status": "added"}]
    assert "+hello" in (await client.get(f"/api/sessions/{sid}/diff", params={"path": "made.txt"})).text

    fork = (await client.post(f"/api/sessions/{sid}/fork", json={})).json()
    assert fork["title"].startswith("Fork:") and len(fork["messages"]) == len(store.get(sid).data["messages"])
    assert store.get(fork["id"]).data["history"][0] == {"role": "user", "content": "make a file"}

    res = (await client.post(f"/api/sessions/{sid}/undo")).json()
    assert res["text"] == "make a file" and "made.txt" in res["files"]
    assert not (workspace / "made.txt").exists()
    s = store.get(sid)
    assert all(m.get("reverted") for m in s.data["messages"]) and s.data["history"] == []


async def test_goal_mode_stops_when_achieved(client, fake_server, workspace):
    configure(fake_server, workspace)
    sid = await new_session(client)
    r = await client.post(f"/api/sessions/{sid}/goal", json={"condition": "the workspace is listed"})
    assert r.json()["goal"]["status"] == "active"
    s = await wait_idle(sid)
    assert s.data["goal"]["status"] == "achieved"
    assert any(p["type"] == "notice" and p["text"].startswith("Goal achieved") for p in parts(s.data))


async def test_question_tool_round_trip(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace)
    original = fake_llm.brain

    def brain(messages):
        if "BUILD mode" in str(messages[0].get("content", "")):
            if messages[-1].get("role") == "user":
                return "", [{"name": "question", "arguments": {"questions": [{"question": "Which DB?", "options": ["sqlite", "postgres"]}]}}]
            return f"Using {json.dumps(messages[-1]['content'])}", []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "set up storage", "agent": "build"})
    pending = []
    for _ in range(200):
        pending = (await client.get(f"/api/sessions/{sid}")).json()["pendingQuestions"]
        if pending:
            break
        await asyncio.sleep(0.05)
    assert pending and pending[0]["questions"][0]["options"] == ["sqlite", "postgres"]
    await client.post(f"/api/sessions/{sid}/questions/{pending[0]['id']}", json={"answers": [["sqlite"]]})
    s = await wait_idle(sid)
    assert any("sqlite" in p.get("text", "") for p in parts(s.data) if p["type"] == "text")


async def test_doom_loop_guard(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace)
    original = fake_llm.brain

    def brain(messages):
        if "BUILD mode" in str(messages[0].get("content", "")):
            return "again", [{"name": "list", "arguments": {"path": "."}}]
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "loop", "agent": "build"})
    s = await wait_idle(sid)
    tools = [p for p in parts(s.data) if p["type"] == "tool"]
    assert len(tools) == 2  # 2 real calls; the 3rd and 4th identical calls are blocked, then the agent is stopped
    assert any(p["type"] == "error" and "kept repeating" in p["text"] for p in parts(s.data))
    assert [e for e in journal.read(sid) if e["kind"] == "loop"]


async def test_parallel_waves_respect_file_ownership(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace, flow={"maxParallel": 3, "fastAcceptLight": True, "mergeSmallTasks": False})
    original = fake_llm.brain
    T = fake_llm._task

    def brain(messages):
        system = str(messages[0].get("content", ""))
        if "Phase: PLAN" in system and fake_llm._assistant_turns(messages) > 0:
            return json.dumps({"title": "Parallel", "summary": "three tasks", "mode": "simple", "questions": [], "modules": [],
                               "verifyCommands": [], "tasks": [
                T("Alpha part", "light", "S", 1, ["alpha.txt"], [], "write alpha", "test -f alpha-part.txt"),
                T("Beta part", "light", "S", 1, ["beta.txt"], [], "write beta", "test -f beta-part.txt"),
                T("Gamma part", "light", "S", 1, ["alpha.txt"], [], "touch alpha again", "test -f gamma-part.txt"),
            ]}), []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    fake_llm.STATE["delay"] = 0.01
    try:
        sid = await new_session(client)
        await client.post(f"/api/sessions/{sid}/message", json={"text": "three things", "agent": "flow"})
        s = await wait_idle(sid, 60)
    finally:
        fake_llm.STATE["delay"] = 0
    flow = s.data["flow"]
    a, b, c = flow["tasks"]
    assert c["after"] == [1] and b["after"] == []          # gamma shares alpha.txt with alpha
    assert flow["stats"]["waves"] == 2 and flow["stats"]["maxWidth"] == 2
    runs = {r["id"]: r for r in s.data["runs"]}
    ra, rb, rc = (runs[t["runIds"][0]] for t in (a, b, c))
    assert rb["startedAt"] < ra["endedAt"]                  # alpha and beta overlapped
    assert rc["startedAt"] >= ra["endedAt"]                 # gamma waited for alpha
    assert flow["done"] == 3
