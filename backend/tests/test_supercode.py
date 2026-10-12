"""Auto mode, project locations, Bitbucket pull requests, masked secrets."""
import asyncio
import json
import shutil
import subprocess

import httpx
import pytest

import fake_llm
from backend import agent, bitbucket, journal
from backend.config import SECRET, save_config
from backend.main import app
from backend.session import store
from conftest import BB


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def configure(fake_server, workspace, **extra):
    agent._text_protocol.clear()
    cfg = {
        "llm": {"provider": "ollama", "baseUrl": fake_server, "model": "fake-coder:7b", "toolMode": "auto", "apiKey": ""},
        "workspace": str(workspace), "autoMode": False,
        "permissions": {"edit": "allow", "bash": "ask"},
        "flow": {"requirePlanApproval": True, "maxParallel": 1, "fastAcceptLight": True, "autoVerify": True, "tokenBudget": 0},
        "agent": {"retryBaseSeconds": 0.01},
        "bitbucket": {"kind": "server", "baseUrl": "", "project": "", "repo": "", "token": "", "username": "",
                      "autoPrOnFlow": False, "targetBranch": "", "reviewers": []},
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


# ---------------------------------------------------------------- auto mode
async def test_auto_mode_flow_needs_no_human(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace, autoMode=True)
    original = fake_llm.brain
    asked = {"n": 0}

    def brain(messages):
        system = str(messages[0].get("content", ""))
        if "Phase: PLAN" in system and fake_llm._assistant_turns(messages) > 0 and asked["n"] == 0:
            asked["n"] += 1
            return json.dumps({"title": "x", "summary": "", "questions": ["Which database?"], "tasks": [], "modules": []}), []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(f"/api/sessions/{sid}/message", json={"text": "greeting", "agent": "flow"})
    s = await wait_idle(sid)
    flow = s.data["flow"]
    # questions answered by the orchestrator itself, plan auto-approved, verify commands (bash: ask) auto-allowed
    assert flow["isFinished"] and flow["done"] == 2 and not s.pending_permissions()
    assert any("AUTO MODE" in (m.get("content") or "") for m in s.flow_engine.plan_history if m["role"] == "user")
    assert any(e.get("auto") for e in journal.read(sid) if e["kind"] == "question")


async def test_auto_mode_question_tool_and_permissions(client, fake_server, workspace, monkeypatch):
    configure(fake_server, workspace)
    original = fake_llm.brain

    def brain(messages):
        if "BUILD mode" in str(messages[0].get("content", "")):
            last = messages[-1]
            if last.get("role") == "user":
                return "", [{"name": "question", "arguments": {"questions": [{"question": "Which DB?", "options": ["a", "b"]}]}},
                            {"name": "bash", "arguments": {"command": "printf ok"}}]
            return "done", []
        return original(messages)

    monkeypatch.setattr(fake_llm, "brain", brain)
    sid = (await client.post("/api/sessions", json={"autoMode": True})).json()["id"]
    assert "AUTO MODE" in __import__("backend.main", fromlist=["_spec_for"])._spec_for(store.get(sid), "build").system
    await client.post(f"/api/sessions/{sid}/message", json={"text": "go", "agent": "build"})
    s = await wait_idle(sid)
    tools = {p["tool"]: p for p in parts(s.data) if p["type"] == "tool"}
    assert "No user is available" in tools["question"]["output"]
    assert tools["bash"]["status"] == "done" and "ok" in tools["bash"]["output"]
    # per-session override back to manual
    r = await client.patch(f"/api/sessions/{sid}", json={"autoMode": False})
    assert r.json()["autoMode"] is False


# ---------------------------------------------------------------- project locations
async def test_folder_browser_and_new_project(client, fake_server, workspace, tmp_path):
    configure(fake_server, workspace, projectsRoot=str(tmp_path / "projects"))
    listing = (await client.get("/api/fs/list", params={"path": str(workspace.parent)})).json()
    assert listing["path"] == str(workspace.parent.resolve()) and any(d["name"] == "ws" for d in listing["dirs"])
    assert listing["roots"]
    bad = await client.post("/api/projects", json={"parent": str(tmp_path / "projects"), "name": "a/b"})
    assert bad.status_code == 400
    r = await client.post("/api/projects", json={"parent": str(tmp_path / "projects"), "name": "My App", "gitInit": True})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert (tmp_path / "projects" / "My App").is_dir()
    if shutil.which("git"):
        assert (tmp_path / "projects" / "My App" / ".git").is_dir()
    s = (await client.post("/api/sessions", json={"directory": path})).json()
    assert s["directory"] == path
    cfg = (await client.get("/api/config")).json()
    assert cfg["recentProjects"][0] == path
    assert (await client.post("/api/projects", json={"parent": str(tmp_path / "projects"), "name": "My App"})).status_code in (200, 409)


async def test_secrets_are_masked(client, fake_server, workspace):
    configure(fake_server, workspace, bitbucket={"token": "s3cret", "kind": "server"})
    cfg = (await client.get("/api/config")).json()
    assert cfg["bitbucket"]["token"] == SECRET
    cfg["bitbucket"]["project"] = "PROJ"
    saved = (await client.put("/api/config", json=cfg)).json()
    assert saved["bitbucket"]["token"] == SECRET
    from backend.config import load_config
    assert load_config()["bitbucket"]["token"] == "s3cret" and load_config()["bitbucket"]["project"] == "PROJ"


# ---------------------------------------------------------------- Bitbucket
@pytest.mark.parametrize("url,expected", [
    ("https://bitbucket.company.com/scm/PROJ/my-repo.git", ("server", "PROJ", "my-repo", "https://bitbucket.company.com")),
    ("https://bb.corp/context/scm/AB/svc.git", ("server", "AB", "svc", "https://bb.corp/context")),
    ("ssh://git@bitbucket.company.com:7999/proj/my-repo.git", ("server", "proj", "my-repo", "https://bitbucket.company.com")),
    ("https://user@bitbucket.org/acme/web.git", ("cloud", "acme", "web", "")),
    ("git@bitbucket.org:acme/web.git", ("cloud", "acme", "web", "")),
])
def test_parse_remote(url, expected):
    r = bitbucket.parse_remote(url)
    assert (r["kind"], r["project"], r["repo"], r["baseUrl"]) == expected


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_project(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git missing")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init", "-q", "-b", "develop")
    _git(proj, "config", "user.email", "t@t")
    _git(proj, "config", "user.name", "t")
    (proj / "README.md").write_text("# p\n")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "init")
    _git(proj, "remote", "add", "origin", str(remote))
    _git(proj, "push", "-q", "origin", "develop")
    return proj, remote


@pytest.mark.parametrize("kind", ["server", "cloud"])
async def test_pull_request_end_to_end(client, fake_server, bitbucket_server, git_project, kind):
    proj, remote = git_project
    BB["calls"].clear()
    BB["existing"] = False
    base = bitbucket_server if kind == "server" else f"{bitbucket_server}/2.0"
    configure(fake_server, proj, bitbucket={"kind": kind, "baseUrl": base, "project": "PROJ", "repo": "app",
                                             "token": "tok", "username": "" if kind == "server" else "me",
                                             "reviewers": ["alice"], "branchPrefix": "supercode/"})
    sid = (await client.post("/api/sessions", json={"directory": str(proj)})).json()["id"]
    (proj / "feature.py").write_text("x = 1\n")
    status = (await client.get(f"/api/sessions/{sid}/git")).json()
    assert status["isRepo"] and status["branch"] == "develop" and status["changes"]
    assert status["draft"]["description"].endswith("_Created with DOINg.AI SuperCode._")
    test = (await client.post("/api/bitbucket/test", json={"directory": str(proj)})).json()
    assert test["ok"], test

    r = await client.post(f"/api/sessions/{sid}/pr", json={"title": "Add feature", "description": "desc"})
    assert r.status_code == 200, r.text
    pr = r.json()
    assert pr["branch"].startswith("supercode/add-feature-") and pr["steps"] == ["branch " + pr["branch"], "commit", "push", "pull request"]
    # the branch really reached the remote with the commit
    heads = subprocess.run(["git", "--git-dir", str(remote), "branch"], capture_output=True, text=True).stdout
    assert pr["branch"] in heads
    call = [c for c in BB["calls"] if c[0].startswith("POST")][-1]
    body = call[4]
    if kind == "server":
        assert pr["url"] == "https://bb.local/projects/PROJ/repos/app/pull-requests/7"
        assert pr["target"] == "develop"  # repository default branch
        assert body["fromRef"]["id"] == f"refs/heads/{pr['branch']}" and body["toRef"]["id"] == "refs/heads/develop"
        assert body["reviewers"] == [{"user": {"name": "alice"}}] and call[3] == "Bearer tok"
    else:
        assert pr["url"] == "https://bitbucket.org/PROJ/app/pull-requests/12" and pr["target"] == "main"
        assert body["source"]["branch"]["name"] == pr["branch"] and call[3].startswith("Basic ")
    assert store.get(sid).data["pullRequests"][0]["url"] == pr["url"]
    assert any(e["kind"] == "pr" for e in journal.read(sid))


async def test_pull_request_existing_and_errors(client, fake_server, bitbucket_server, git_project):
    proj, _ = git_project
    BB["existing"] = True
    configure(fake_server, proj, bitbucket={"kind": "server", "baseUrl": bitbucket_server, "project": "PROJ", "repo": "app", "token": "tok"})
    sid = (await client.post("/api/sessions", json={"directory": str(proj)})).json()["id"]
    (proj / "f.txt").write_text("1\n")
    pr = (await client.post(f"/api/sessions/{sid}/pr", json={"title": "Again", "branch": "feature/x"})).json()
    assert pr["existing"] and pr["url"] == "https://bb.local/pr/3" and pr["branch"] == "feature/x"
    BB["existing"] = False
    bad = await client.post(f"/api/sessions/{sid}/pr", json={"title": "x", "branch": "develop", "target": "develop"})
    assert bad.status_code == 400 and "both" in bad.json()["detail"]
