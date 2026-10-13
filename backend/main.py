"""HTTP API + SSE for the React UI, and static hosting of the built frontend."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bitbucket, compaction, journal, llm, prompts
from .agent import AgentSpec, _manage_context, make_context, run_agent
from .config import (CONFIG_FILE, ROOT_DIR, auto_mode, load_config, public_config, read_nexus_llm_config,
                     remember_project, save_config, strip_masked)
from .flow import FlowEngine, extract_json, resume_engine, run_worker
from .session import MessageSink, Session, new_id, now_ms, store
from .osutil import posix, read_text, run_command
from .snapshot import Snapshots
from .tools import IGNORED_DIRS, READ_ONLY

DIST_DIR = ROOT_DIR / "frontend" / "dist"
AGENTS = ("build", "plan", "flow")
MAX_AUTO_RESUMES = 5


def first_run_nexus_import() -> None:
    """First launch: reuse the LLM configured in a NEXUS.AI checkout next to this one."""
    if CONFIG_FILE.exists():
        return
    try:
        llm_cfg, path = read_nexus_llm_config("")
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
        return
    save_config({"llm": llm_cfg, "nexusDbPath": str(path)})
    print(f"[supercode] LLM settings imported from NEXUS.AI: {path}")


def resume_interrupted_flows() -> list[str]:
    """After a crash or restart, continue the flows that were executing (OpenCode-style recovery)."""
    if not load_config()["flow"].get("autoResume", True):
        return []
    resumed = []
    store.load_all()
    for s in list(store.sessions.values()):
        flow = s.data.get("flow")
        if not flow or flow.get("isFinished") or not flow.get("wasExecuting") or s.busy:
            continue
        flow["wasExecuting"] = False
        if flow.get("resumeCount", 0) >= MAX_AUTO_RESUMES:
            journal.log(s, "error", error="flow interrupted too many times: not resumed automatically")
            continue
        flow["resumeCount"] = flow.get("resumeCount", 0) + 1
        s.flow_engine = resume_engine(s)
        journal.log(s, "resume", attempt=flow["resumeCount"])
        MessageSink(s).add_message("assistant", "system", parts=[{
            "type": "notice", "text": f"Server restarted: resuming the flow (attempt {flow['resumeCount']}/{MAX_AUTO_RESUMES})."}])
        _start(s, s.flow_engine.execute())
        resumed.append(s.id)
    return resumed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    first_run_nexus_import()
    resume_interrupted_flows()
    yield
    for s in store.sessions.values():
        s.save()


app = FastAPI(title="DOINg.AI SuperCode", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# models


class ConfigUpdate(BaseModel):
    model_config = {"extra": "allow"}


class TestRequest(BaseModel):
    llm: Optional[dict] = None


class NexusImport(BaseModel):
    path: str = ""


class SessionCreate(BaseModel):
    agent: str = "build"
    directory: str = ""
    autoMode: Optional[bool] = None


class SessionPatch(BaseModel):
    title: Optional[str] = None
    agent: Optional[str] = None
    autoMode: Optional[bool] = None  # null = follow the global setting


class MessageIn(BaseModel):
    text: str
    agent: Optional[str] = None


class PermissionReply(BaseModel):
    response: str  # once | always | reject


class QuestionReply(BaseModel):
    answers: list[Any]


class ForkIn(BaseModel):
    messageId: Optional[str] = None


class GoalIn(BaseModel):
    condition: Optional[str] = None


class PathIn(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# config


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
async def get_config() -> dict:
    return public_config(load_config())


@app.put("/api/config")
async def put_config(body: ConfigUpdate) -> dict:
    data = strip_masked(body.model_dump())
    if "workspace" in data and data["workspace"]:
        p = Path(data["workspace"]).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"Workspace folder not found: {p}")
        data["workspace"] = str(p.resolve())
    return public_config(save_config(data))


@app.post("/api/config/test")
async def test_config(body: TestRequest) -> dict:
    cfg = load_config()["llm"]
    if body.llm:
        cfg = {**cfg, **strip_masked({"llm": dict(body.llm)})["llm"]}
    return await llm.test_connection(cfg)


@app.post("/api/config/import-nexus")
async def import_nexus(body: NexusImport) -> dict:
    try:
        llm_cfg, path = read_nexus_llm_config(body.path or load_config().get("nexusDbPath", ""))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, str(e)) from e
    cfg = save_config({"llm": llm_cfg, "nexusDbPath": str(path)})
    return {"config": public_config(cfg), "source": str(path)}


# ---------------------------------------------------------------------------
# sessions


def _get(sid: str) -> Session:
    s = store.get(sid)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@app.get("/api/sessions")
async def list_sessions() -> list:
    return store.list()


@app.post("/api/sessions")
async def create_session(body: SessionCreate) -> dict:
    directory = str(Path(body.directory or load_config()["workspace"]).expanduser())
    if not Path(directory).is_dir():
        raise HTTPException(400, f"Folder not found: {directory}")
    directory = str(Path(directory).resolve())
    s = store.create(directory, body.agent if body.agent in AGENTS else "build")
    if body.autoMode is not None:
        s.data["autoMode"] = body.autoMode
    remember_project(directory)
    return s.full()


# ---------------------------------------------------------------------------
# project locations: folder browser and "new project"

INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _roots() -> list[str]:
    if os.name == "nt":
        import string
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    return ["/"]


@app.get("/api/fs/list")
async def fs_list(path: str = "", hidden: bool = False) -> dict:
    """Sub-folders of ``path`` (default: the projects root, else home) for the folder picker."""
    cfg = load_config()
    if path:
        target = Path(path).expanduser()
    else:
        root = Path(cfg.get("projectsRoot") or Path.home()).expanduser()
        target = root if root.is_dir() else Path.home()
    if not target.is_dir():
        raise HTTPException(400, f"Folder not found: {target}")
    target = target.resolve()
    dirs = []
    try:
        for entry in sorted(os.scandir(target), key=lambda e: e.name.lower()):
            try:
                if entry.is_dir() and (hidden or not entry.name.startswith(".")) and entry.name not in IGNORED_DIRS:
                    dirs.append({"name": entry.name, "path": str(Path(entry.path)),
                                 "isProject": any((Path(entry.path) / m).exists() for m in (".git", "package.json", "pyproject.toml", "AGENTS.md"))})
            except OSError:
                continue
    except PermissionError as e:
        raise HTTPException(403, f"Cannot open {target}: {e}") from e
    parent = target.parent if target.parent != target else None
    return {"path": str(target), "parent": str(parent) if parent else None, "dirs": dirs[:500], "roots": _roots(),
            "home": str(Path.home()), "projectsRoot": cfg.get("projectsRoot"), "recent": cfg.get("recentProjects", []),
            "sep": os.sep}


class ProjectCreate(BaseModel):
    parent: str
    name: str
    gitInit: bool = True


@app.post("/api/projects")
async def create_project(body: ProjectCreate) -> dict:
    name = body.name.strip()
    if not name or INVALID_NAME.search(name) or name in (".", ".."):
        raise HTTPException(400, "Invalid project name (avoid < > : \" / \\ | ? *)")
    parent = Path(body.parent or load_config().get("projectsRoot") or Path.home()).expanduser()
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"Cannot create {parent}: {e}") from e
    target = (parent / name).resolve()
    if target.exists() and any(target.iterdir()):
        raise HTTPException(409, f"{target} already exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)
    if body.gitInit:
        code, out, _ = await run_command("git init -q", str(target), 60)
        if code != 0:
            raise HTTPException(500, f"git init failed: {out[:300]}")
    remember_project(str(target))
    return {"path": str(target)}


@app.get("/api/sessions/{sid}")
async def get_session(sid: str) -> dict:
    return _get(sid).full()


@app.patch("/api/sessions/{sid}")
async def patch_session(sid: str, body: SessionPatch) -> dict:
    s = _get(sid)
    if body.title is not None:
        s.data["title"] = body.title.strip()[:120] or s.data["title"]
    if body.agent in AGENTS:
        s.data["agent"] = body.agent
    if "autoMode" in body.model_fields_set:
        s.data["autoMode"] = body.autoMode
        journal.log(s, "auto_mode", value=s.data["autoMode"])
    s.touch()
    s.emit("session.updated", {"session": s.summary()})
    return s.summary()


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str) -> dict:
    if not store.delete(sid):
        raise HTTPException(404, "Session not found")
    return {"ok": True}


@app.get("/api/sessions/{sid}/events")
async def session_events(sid: str) -> StreamingResponse:
    s = _get(sid)
    q: asyncio.Queue = asyncio.Queue(maxsize=20000)
    s.subscribers.add(q)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'busy': s.busy})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            s.subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/sessions/{sid}/runs/{rid}")
async def get_run(sid: str, rid: str) -> dict:
    run = _get(sid).run(rid)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.post("/api/sessions/{sid}/abort")
async def abort(sid: str) -> dict:
    s = _get(sid)
    if s.task and not s.task.done():
        s.task.cancel()
    return {"ok": True}


@app.post("/api/sessions/{sid}/permissions/{pid}")
async def reply_permission(sid: str, pid: str, body: PermissionReply) -> dict:
    s = _get(sid)
    fut = s.permissions.get(pid)
    if not fut or fut.done():
        raise HTTPException(404, "No pending permission with this id")
    fut.set_result(body.response if body.response in ("once", "always", "reject") else "reject")
    return {"ok": True}


@app.post("/api/sessions/{sid}/questions/{qid}")
async def reply_question(sid: str, qid: str, body: QuestionReply) -> dict:
    s = _get(sid)
    fut = s.questions.get(qid)
    if not fut or fut.done():
        raise HTTPException(404, "No pending question with this id")
    fut.set_result(body.answers)
    return {"ok": True}


@app.post("/api/sessions/{sid}/flow/approve")
async def approve_flow(sid: str) -> dict:
    s = _get(sid)
    flow = s.data.get("flow")
    if not flow or flow.get("isFinished"):
        raise HTTPException(400, "No flow to approve")
    if s.busy:
        raise HTTPException(409, "Session is busy")
    if flow.get("awaiting") != "approval" and not flow.get("interrupted"):
        raise HTTPException(400, "The flow is not waiting for approval")
    if not s.flow_engine:
        s.flow_engine = resume_engine(s)
    journal.log(s, "approve", flow=flow["id"])
    _start(s, s.flow_engine.approve())
    return {"ok": True}


# ---------------------------------------------------------------------------
# traceability: changes, journal, report, undo


@app.get("/api/sessions/{sid}/changes")
async def changes(sid: str) -> dict:
    s = _get(sid)
    base = s.data.get("baseSnapshot")
    files = await Snapshots(s.directory).changed(base) if base else []
    return {"base": base, "files": files}


@app.get("/api/sessions/{sid}/diff")
async def file_diff(sid: str, path: str = "") -> PlainTextResponse:
    s = _get(sid)
    base = s.data.get("baseSnapshot")
    return PlainTextResponse(await Snapshots(s.directory).diff(base, path=path or None) if base else "")


@app.post("/api/sessions/{sid}/revert-file")
async def revert_file(sid: str, body: PathIn) -> dict:
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "Session is busy")
    base = s.data.get("baseSnapshot")
    if not base:
        raise HTTPException(400, "No snapshot for this session")
    touched = await Snapshots(s.directory).restore(base, [body.path])
    journal.log(s, "revert", files=touched)
    return {"files": touched}


@app.post("/api/sessions/{sid}/undo")
async def undo(sid: str) -> dict:
    """Restore the files as they were before the last user turn and drop that turn (OpenCode /undo)."""
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "Session is busy")
    msgs = s.data["messages"]
    idx = next((i for i in range(len(msgs) - 1, -1, -1)
                if msgs[i]["role"] == "user" and msgs[i].get("snapshot") and not msgs[i].get("reverted")), None)
    if idx is None:
        raise HTTPException(400, "Nothing to undo")
    msg = msgs[idx]
    touched = await Snapshots(s.directory).restore(msg["snapshot"])
    for m in msgs[idx:]:
        m["reverted"] = True
    s.data["history"] = s.data.get("history", [])[: msg.get("historyLen", 0)]
    text = " ".join(p.get("text", "") for p in msg["parts"] if p.get("type") == "text")
    journal.log(s, "revert", files=touched, undo=True)
    s.touch()
    s.emit("session.reload", {})
    return {"text": text, "files": touched}


# ---------------------------------------------------------------------------
# git + Bitbucket pull requests


class PullRequestIn(BaseModel):
    title: str
    description: str = ""
    branch: str = ""
    target: str = ""
    commitMessage: str = ""


class BitbucketTest(BaseModel):
    directory: str = ""
    bitbucket: Optional[dict] = None


async def _diffstat(directory: str, st: dict) -> str:
    if not st.get("isRepo"):
        return ""
    if st.get("hasCommits"):
        _, out = await bitbucket.git(directory, "diff", "--stat", "HEAD")
    else:
        out = ""
    untracked = [l[3:] for l in st.get("changes", []) if l.startswith("??")]
    if untracked:
        out += ("\n" if out else "") + "\n".join(f" {u} (new)" for u in untracked[:50])
    return out


@app.get("/api/sessions/{sid}/git")
async def git_status(sid: str) -> dict:
    s = _get(sid)
    cfg = load_config()["bitbucket"]
    st = await bitbucket.status(s.directory, cfg.get("remote") or "origin")
    draft = bitbucket.draft_description(s.data, await _diffstat(s.directory, st))
    configured = bool(load_config()["bitbucket"].get("token"))
    try:
        target = bitbucket.resolve_target(cfg, st) if st.get("isRepo") else None
    except bitbucket.BitbucketError as e:
        target = {"error": str(e)}
    return {**st, "draft": draft, "configured": configured, "target": target,
            "pullRequests": s.data.get("pullRequests", [])}


@app.post("/api/sessions/{sid}/pr")
async def create_pr(sid: str, body: PullRequestIn) -> dict:
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "Session is busy: wait for the agent to finish")
    cfg = load_config()["bitbucket"]
    try:
        pr = await bitbucket.create_pull_request(s.directory, cfg, title=body.title.strip() or "Changes",
                                                 description=body.description, branch=body.branch, target=body.target,
                                                 commit_message=body.commitMessage)
    except bitbucket.BitbucketError as e:
        journal.log(s, "error", error=f"pull request: {e}")
        raise HTTPException(400, str(e)) from e
    entry = {**pr, "title": body.title, "createdAt": now_ms()}
    s.data.setdefault("pullRequests", []).append(entry)
    journal.log(s, "pr", url=pr["url"], branch=pr["branch"], target=pr["target"], steps=pr["steps"])
    s.emit("pr.created", {"pr": entry})
    s.touch()
    return entry


@app.post("/api/bitbucket/test")
async def bitbucket_test(body: BitbucketTest) -> dict:
    cfg = load_config()["bitbucket"]
    if body.bitbucket:
        cfg = {**cfg, **strip_masked({"bitbucket": dict(body.bitbucket)})["bitbucket"]}
    try:
        return await bitbucket.test(cfg, body.directory or "")
    except (bitbucket.BitbucketError, httpx.HTTPError) as e:
        return {"ok": False, "message": str(e)}


@app.get("/api/sessions/{sid}/journal")
async def get_journal(sid: str, limit: int = 1000) -> list:
    _get(sid)
    return journal.read(sid, limit)


@app.get("/api/sessions/{sid}/report")
async def get_report(sid: str) -> PlainTextResponse:
    s = _get(sid)
    data = {**s.data, "_journal": journal.read(sid, 2000)}
    return PlainTextResponse(journal.report(data), media_type="text/markdown")


@app.post("/api/sessions/{sid}/fork")
async def fork(sid: str, body: ForkIn) -> dict:
    """Branch the conversation into a new session (OpenClaude --fork-session)."""
    s = _get(sid)
    msgs = s.data["messages"]
    end = len(msgs)
    if body.messageId:
        idx = next((i for i, m in enumerate(msgs) if m["id"] == body.messageId), None)
        if idx is None:
            raise HTTPException(404, "Message not found")
        end = idx + 1
    new = store.create(s.directory, s.data["agent"], f"Fork: {s.data['title']}"[:120])
    kept = [m for m in copy.deepcopy(msgs[:end]) if not m.get("reverted")]
    new.data["messages"] = kept
    # A compact LLM history: text turns, tool activity summarized (cheaper than replaying outputs).
    history: list[dict] = []
    for m in kept:
        text = "\n".join(p.get("text", "") for p in m["parts"] if p.get("type") == "text" and not p.get("hidden"))
        tools = [f"{p['tool']} {p.get('title') or ''}".strip() for p in m["parts"] if p.get("type") == "tool"]
        if tools:
            text += ("\n" if text else "") + "[tools used: " + ", ".join(tools)[:500] + "]"
        if not text.strip():
            continue
        role = "user" if m["role"] == "user" else "assistant"
        if history and history[-1]["role"] == role:
            history[-1]["content"] += "\n\n" + text
        else:
            history.append({"role": role, "content": text})
    new.data["history"] = history
    new.data["baseSnapshot"] = s.data.get("baseSnapshot")
    journal.log(new, "fork", source=sid, messages=len(kept))
    new.save()
    return new.full()


@app.post("/api/sessions/{sid}/compact")
async def compact(sid: str) -> dict:
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "Session is busy")
    if len(s.data.get("history", [])) < 4:
        raise HTTPException(400, "Nothing to compact yet")

    async def run() -> None:
        cfg = load_config()
        spec = _spec_for(s, "build")
        await _manage_context(s, MessageSink(s), spec, cfg["llm"], cfg["context"], s.data["history"],
                              int(cfg["llm"].get("contextWindow") or 16384), force=True)

    _start(s, run())
    return {"ok": True}


@app.post("/api/sessions/{sid}/goal")
async def set_goal(sid: str, body: GoalIn) -> dict:
    """Goal mode (OpenClaude): keep working until an evaluator says the condition holds."""
    s = _get(sid)
    condition = (body.condition or "").strip()
    if not condition:
        s.data["goal"] = None
        s.emit("session.updated", {"session": s.summary()})
        s.touch()
        return {"goal": None}
    s.data["goal"] = {"condition": condition, "status": "active", "continuations": 0, "createdAt": now_ms(), "reason": ""}
    journal.log(s, "goal", condition=condition)
    s.emit("goal.updated", {"goal": s.data["goal"]})
    if not s.busy:
        text = (f"A session goal has been set.\n\nGoal condition:\n{condition}\n\nWork directly toward this goal. Do not stop "
                "because one turn ended; stop when the goal is complete, when a user decision is needed, or when blocked.")
        MessageSink(s).add_message("user", "build", parts=[{"id": new_id("prt"), "type": "text", "text": text, "goal": True}])
        s.data["agent"] = "build"
        _start(s, _chat_turn(s, "build", text))
    return {"goal": s.data["goal"]}


# ---------------------------------------------------------------------------
# chat


FILE_MENTION = re.compile(r"(?<![\w/])@([\w./\-]+)")


def _attach_mentions(directory: str, text: str) -> str:
    """Inline the content of @path mentions, like OpenCode does."""
    root = Path(directory).resolve()
    blocks = []
    for raw in dict.fromkeys(FILE_MENTION.findall(text)):
        p = (root / raw).resolve()
        if root not in p.parents or not p.is_file() or p.stat().st_size > 200_000:
            continue
        try:
            content = read_text(p)
        except (OSError, UnicodeDecodeError):
            continue
        blocks.append(f'<file path="{raw}">\n{content}\n</file>')
    return text + ("\n\n" + "\n\n".join(blocks) if blocks else "")


def _start(s: Session, coro) -> None:
    async def runner():
        s.emit("status", {"busy": True})
        try:
            await coro
        except asyncio.CancelledError:
            MessageSink(s).add_message("assistant", "system", parts=[{"type": "notice", "text": "Interrupted."}])
            journal.log(s, "abort")
        except llm.LLMError as e:
            MessageSink(s).add_message("assistant", "system", parts=[{"type": "error", "text": str(e)}])
            journal.log(s, "error", error=str(e)[:500])
        except Exception as e:  # noqa: BLE001 - shown in the transcript rather than lost
            MessageSink(s).add_message("assistant", "system", parts=[{"type": "error", "text": f"{type(e).__name__}: {e}"}])
            journal.log(s, "error", error=f"{type(e).__name__}: {e}"[:500])
        finally:
            s.task = None
            s.emit("status", {"busy": False})
            s.emit("session.updated", {"session": s.summary()})
            s.save()

    s.task = asyncio.create_task(runner())


def _spec_for(s: Session, agent: str) -> AgentSpec:
    cfg = load_config()
    custom = cfg["llm"].get("systemPrompt", "")
    auto = auto_mode(cfg, s.data)
    if agent == "plan":
        return AgentSpec("plan", prompts.system(s.directory, custom, prompts.PLAN, auto=auto),
                         READ_ONLY + ["bash", "todowrite", "todoread", "question", "webfetch"], cfg["llm"]["model"],
                         permissions={"edit": "deny", "bash": "ask"}, pinned=0)
    return AgentSpec("build", prompts.system(s.directory, custom, prompts.BUILD, auto=auto),
                     READ_ONLY + ["write", "edit", "multiedit", "apply_patch", "bash", "webfetch", "todowrite", "todoread",
                                  "question", "task"],
                     cfg["llm"]["model"], pinned=0)


async def _evaluate_goal(s: Session, goal: dict) -> dict:
    cfg = load_config()
    transcript = compaction._render(s.data.get("history", [])[-30:], 12000)
    res = await llm.chat(cfg["llm"], model=cfg["llm"]["model"], temperature=0, json_mode=True, messages=[
        {"role": "system", "content": prompts.GOAL_EVALUATOR},
        {"role": "user", "content": f"Goal condition:\n{goal['condition']}\n\nRecent conversation:\n{transcript}"},
    ])
    data = extract_json(res.text) or {}
    return {"complete": bool(data.get("complete")), "reason": str(data.get("reason") or "")[:400],
            "next": str(data.get("next_instruction") or "")[:600]}


async def _chat_turn(s: Session, agent: str, text: str) -> None:
    spec = _spec_for(s, agent)
    ctx = make_context(s, agent)

    async def delegate(args: dict) -> str:
        report, _changes, _run, _checks = await run_worker(
            s, tier=str(args.get("tier") or "medium"), title=str(args.get("description") or "task")[:80],
            brief=str(args.get("prompt") or ""),
        )
        return report

    ctx.delegate = delegate
    history = s.data.setdefault("history", [])
    user_msg = next((m for m in reversed(s.data["messages"]) if m["role"] == "user"), None)
    snapshot = await Snapshots(s.directory).track()
    if snapshot:
        s.data["baseSnapshot"] = s.data.get("baseSnapshot") or snapshot
        if user_msg is not None:
            user_msg.update(snapshot=snapshot, historyLen=len(history))
            journal.log(s, "snapshot", label="turn", tree=snapshot)
    history.append({"role": "user", "content": _attach_mentions(s.directory, text)})
    await run_agent(s, spec, history, MessageSink(s), ctx)

    max_cont = int(load_config()["agent"].get("goalMaxContinuations", 8))
    while agent == "build" and (goal := s.data.get("goal")) and goal.get("status") == "active":
        decision = await _evaluate_goal(s, goal)
        goal["reason"] = decision["reason"]
        if decision["complete"]:
            goal["status"] = "achieved"
        elif goal["continuations"] >= max_cont:
            goal["status"] = "paused"
        journal.log(s, "goal_eval", complete=decision["complete"], reason=decision["reason"], status=goal["status"])
        s.emit("goal.updated", {"goal": goal})
        if goal["status"] != "active":
            label = "Goal achieved" if goal["status"] == "achieved" else f"Goal paused after {max_cont} continuations"
            MessageSink(s).add_message("assistant", "system", parts=[{"type": "notice", "text": f"{label}: {decision['reason']}"}])
            break
        goal["continuations"] += 1
        cont = (f"Continue working toward the session goal.\n\nGoal condition:\n{goal['condition']}\n\n"
                f"Evaluator: {decision['reason']}" + (f"\nNext: {decision['next']}" if decision["next"] else "")
                + "\n\nContinue directly; do not recap.")
        MessageSink(s).add_message("user", "build", parts=[{"id": new_id("prt"), "type": "text", "text": cont, "goal": True}])
        history.append({"role": "user", "content": cont})
        await run_agent(s, spec, history, MessageSink(s), ctx)


@app.post("/api/sessions/{sid}/message")
async def post_message(sid: str, body: MessageIn) -> dict:
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "Session is busy")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty message")
    agent = body.agent if body.agent in AGENTS else s.data["agent"]
    if text.startswith("/flow"):
        agent, text = "flow", text[5:].strip() or text
    s.data["agent"] = agent

    MessageSink(s).add_message("user", agent, parts=[{"id": new_id("prt"), "type": "text", "text": text}])
    journal.log(s, "user", agent=agent, text=text[:2000])
    if s.data["title"] == "New session":
        s.data["title"] = re.sub(r"\s+", " ", text)[:60]
        s.emit("session.updated", {"session": s.summary()})

    flow = s.data.get("flow")
    if agent == "flow":
        awaiting = flow and not flow.get("isFinished") and flow.get("awaiting")
        if awaiting:
            if not s.flow_engine:
                s.flow_engine = resume_engine(s)
            _start(s, s.flow_engine.revise(text))
        else:
            s.flow_engine = FlowEngine(s)
            _start(s, s.flow_engine.start(text))
    else:
        _start(s, _chat_turn(s, agent, text))
    return {"ok": True}


# ---------------------------------------------------------------------------
# files (for @ mentions)


@app.get("/api/sessions/{sid}/files")
async def list_files(sid: str, q: str = "") -> list:
    s = _get(sid)
    root = Path(s.directory)
    q = q.lower()
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for name in filenames:
            relp = posix((Path(dirpath) / name).relative_to(root))
            if not q or q in relp.lower():
                out.append(relp)
                if len(out) >= 50:
                    return sorted(out, key=len)
    return sorted(out, key=len)


# ---------------------------------------------------------------------------
# static frontend

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        target = DIST_DIR / path
        if path and target.is_file() and DIST_DIR in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(DIST_DIR / "index.html")
