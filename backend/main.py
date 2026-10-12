"""HTTP API + SSE for the React UI, and static hosting of the built frontend."""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm, prompts
from .agent import AgentSpec, make_context, run_agent
from .config import CONFIG_FILE, ROOT_DIR, load_config, read_nexus_llm_config, save_config
from .flow import FlowEngine, resume_engine, run_worker
from .session import MessageSink, Session, new_id, store
from .tools import IGNORED_DIRS, READ_ONLY

DIST_DIR = ROOT_DIR / "frontend" / "dist"
AGENTS = ("build", "plan", "flow")

def first_run_nexus_import() -> None:
    """First launch: reuse the LLM configured in a NEXUS.AI checkout next to this one."""
    if CONFIG_FILE.exists():
        return
    try:
        llm_cfg, path = read_nexus_llm_config("")
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
        return
    save_config({"llm": llm_cfg, "nexusDbPath": str(path)})
    print(f"[orchestrator] LLM settings imported from NEXUS.AI: {path}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    first_run_nexus_import()
    yield


app = FastAPI(title="DOINg.AI Orchestrator", lifespan=lifespan)
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


class SessionPatch(BaseModel):
    title: Optional[str] = None
    agent: Optional[str] = None


class MessageIn(BaseModel):
    text: str
    agent: Optional[str] = None


class PermissionReply(BaseModel):
    response: str  # once | always | reject


# ---------------------------------------------------------------------------
# config


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
async def get_config() -> dict:
    return load_config()


@app.put("/api/config")
async def put_config(body: ConfigUpdate) -> dict:
    data = body.model_dump()
    if "workspace" in data and data["workspace"]:
        p = Path(data["workspace"]).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"Workspace folder not found: {p}")
        data["workspace"] = str(p.resolve())
    return save_config(data)


@app.post("/api/config/test")
async def test_config(body: TestRequest) -> dict:
    cfg = load_config()["llm"]
    if body.llm:
        cfg = {**cfg, **body.llm}
    return await llm.test_connection(cfg)


@app.post("/api/config/import-nexus")
async def import_nexus(body: NexusImport) -> dict:
    try:
        llm_cfg, path = read_nexus_llm_config(body.path or load_config().get("nexusDbPath", ""))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, str(e)) from e
    cfg = save_config({"llm": llm_cfg, "nexusDbPath": str(path)})
    return {"config": cfg, "source": str(path)}


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
    directory = body.directory or load_config()["workspace"]
    if not Path(directory).expanduser().is_dir():
        raise HTTPException(400, f"Folder not found: {directory}")
    s = store.create(directory, body.agent if body.agent in AGENTS else "build")
    return s.full()


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
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    s.subscribers.add(q)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'busy': s.busy})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            s.subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
    _start(s, s.flow_engine.approve())
    return {"ok": True}


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
            content = p.read_text(encoding="utf-8")
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
        except llm.LLMError as e:
            MessageSink(s).add_message("assistant", "system", parts=[{"type": "error", "text": str(e)}])
        except Exception as e:  # noqa: BLE001 - shown in the transcript rather than lost
            MessageSink(s).add_message("assistant", "system", parts=[{"type": "error", "text": f"{type(e).__name__}: {e}"}])
        finally:
            s.task = None
            s.emit("status", {"busy": False})
            s.emit("session.updated", {"session": s.summary()})
            s.save()

    s.task = asyncio.create_task(runner())


def _spec_for(s: Session, agent: str) -> AgentSpec:
    cfg = load_config()
    base = prompts.base(s.directory, cfg["llm"].get("systemPrompt", ""))
    if agent == "plan":
        return AgentSpec("plan", f"{base}\n\n{prompts.PLAN}", READ_ONLY + ["bash", "todowrite", "todoread"], cfg["llm"]["model"],
                         permissions={"edit": "deny", "bash": "ask"})
    return AgentSpec("build", f"{base}\n\n{prompts.BUILD}",
                     READ_ONLY + ["write", "edit", "bash", "todowrite", "todoread", "task"], cfg["llm"]["model"])


async def _chat_turn(s: Session, agent: str, text: str) -> None:
    spec = _spec_for(s, agent)
    ctx = make_context(s, agent)

    async def delegate(args: dict) -> str:
        report, _changes, _run = await run_worker(
            s, tier=str(args.get("tier") or "medium"), title=str(args.get("description") or "task")[:80],
            brief=str(args.get("prompt") or ""),
        )
        return report

    ctx.delegate = delegate
    s.data.setdefault("history", []).append({"role": "user", "content": _attach_mentions(s.directory, text)})
    await run_agent(s, spec, s.data["history"], MessageSink(s), ctx)


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
            relp = str((Path(dirpath) / name).relative_to(root))
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
