"""Sessions, transcript parts, agent runs and the per-session event bus.

Everything the UI shows is plain JSON-able dicts, so a session can be persisted as
is and every change can be pushed over SSE as a small event.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR
from .osutil import atomic_write

SESSIONS_DIR = DATA_DIR / "sessions"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    return int(time.time() * 1000)


class Session:
    def __init__(self, data: dict):
        self.data = data
        self.subscribers: set[asyncio.Queue] = set()
        self.task: Optional[asyncio.Task] = None
        self.permissions: dict[str, asyncio.Future] = {}
        self.questions: dict[str, asyncio.Future] = {}
        self.dirty_runs: set[str] = set()
        self.seq = 0
        self.approval: Optional[asyncio.Future] = None
        self.flow_engine: Any = None
        self._save_handle: Optional[asyncio.TimerHandle] = None

    # -- accessors -------------------------------------------------------
    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def directory(self) -> str:
        return self.data["directory"]

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def summary(self) -> dict:
        d = self.data
        return {
            "id": d["id"],
            "title": d["title"],
            "agent": d["agent"],
            "directory": d["directory"],
            "createdAt": d["createdAt"],
            "updatedAt": d["updatedAt"],
            "busy": self.busy,
            "flowTitle": (d.get("flow") or {}).get("title"),
            "goalActive": bool(d.get("goal") and d["goal"].get("status") == "active"),
            "autoMode": d.get("autoMode"),
            "permissionMode": d.get("permissionMode"),
        }

    def full(self) -> dict:
        # The raw LLM history stays server-side; worker transcripts are fetched per run.
        data = {k: v for k, v in self.data.items() if k not in ("history", "runs", "permissionLog", "questionLog")}
        data["runs"] = [{k: v for k, v in r.items() if k != "messages"} for r in self.data.get("runs", [])]
        return {**data, "seq": self.seq, "busy": self.busy, "pendingPermissions": self.pending_permissions(),
                "pendingQuestions": self.pending_questions()}

    def pending_permissions(self) -> list[dict]:
        return [p for p in self.data.setdefault("permissionLog", []) if p.get("status") == "pending"]

    def pending_questions(self) -> list[dict]:
        return [q for q in self.data.setdefault("questionLog", []) if q.get("status") == "pending"]

    def run(self, run_id: str) -> Optional[dict]:
        for r in self.data.get("runs", []):
            if r["id"] == run_id:
                if "messages" not in r:
                    path = SESSIONS_DIR / self.id / f"{run_id}.json"
                    try:
                        r["messages"] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
                    except (OSError, json.JSONDecodeError):
                        r["messages"] = []
                    if r.get("status") != "running":
                        close_parts(r["messages"])
                return r
        return None

    # -- events ----------------------------------------------------------
    def emit(self, event_type: str, payload: dict) -> None:
        # A monotonic sequence lets clients drop events already contained in a snapshot.
        self.seq += 1
        event = {"type": event_type, "seq": self.seq, **payload}
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def touch(self) -> None:
        self.data["updatedAt"] = now_ms()
        self.schedule_save()

    def schedule_save(self, delay: float = 0.4) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save()
            return
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = loop.call_later(delay, self.save)

    def save(self) -> None:
        try:
            self._save()
        except OSError as e:  # disk full, file locked: keep running, retry on next change
            print(f"[supercode] could not save session {self.id}: {e}")

    def _save(self) -> None:
        """Main session file without worker transcripts; each run transcript has its own file."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        runs_dir = SESSIONS_DIR / self.id
        for run in self.data.get("runs", []):
            if run["id"] in self.dirty_runs and "messages" in run:
                runs_dir.mkdir(parents=True, exist_ok=True)
                atomic_write(runs_dir / f"{run['id']}.json", json.dumps(run["messages"], ensure_ascii=False))
        self.dirty_runs.clear()
        data = {**self.data, "runs": [{k: v for k, v in r.items() if k != "messages"} for r in self.data.get("runs", [])]}
        atomic_write(SESSIONS_DIR / f"{self.id}.json", json.dumps(data, ensure_ascii=False))


class MessageSink:
    """Writes one transcript (the main chat, or a worker run) and emits its events."""

    def __init__(self, session: Session, run: Optional[dict] = None):
        self.session = session
        self.run = run

    @property
    def run_id(self) -> Optional[str]:
        return self.run["id"] if self.run else None

    def _messages(self) -> list[dict]:
        if self.run is not None:
            self.session.dirty_runs.add(self.run["id"])
            if "messages" not in self.run:
                self.session.run(self.run["id"])
            return self.run["messages"]
        return self.session.data["messages"]

    def add_message(self, role: str, agent: str, model: str = "", parts: Optional[list] = None) -> dict:
        msg = {
            "id": new_id("msg"),
            "role": role,
            "agent": agent,
            "model": model,
            "createdAt": now_ms(),
            "parts": parts or [],
            "tokens": {"input": 0, "output": 0},
        }
        self._messages().append(msg)
        self.session.emit("message.created", {"runId": self.run_id, "message": msg})
        self.session.touch()
        return msg

    def add_part(self, msg: dict, part: dict) -> dict:
        part.setdefault("id", new_id("prt"))
        msg["parts"].append(part)
        self.update_part(msg, part)
        return part

    def update_part(self, msg: dict, part: dict) -> None:
        if self.run is not None:
            self.session.dirty_runs.add(self.run["id"])
        self.session.emit("part.updated", {"runId": self.run_id, "messageId": msg["id"], "part": part})
        self.session.touch()

    def delta(self, msg: dict, part: dict, chunk: str) -> None:
        part["text"] = part.get("text", "") + chunk
        self.session.emit(
            "part.delta", {"runId": self.run_id, "messageId": msg["id"], "partId": part["id"], "delta": chunk}
        )

    def update_message(self, msg: dict) -> None:
        self.session.emit("message.updated", {"runId": self.run_id, "message": {k: v for k, v in msg.items() if k != "parts"}})
        self.session.touch()


class SessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self._loaded = False

    def load_all(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not SESSIONS_DIR.exists():
            return
        for path in SESSIONS_DIR.glob("ses_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            _recover_interrupted(data)
            self.sessions[data["id"]] = Session(data)

    def list(self) -> list[dict]:
        self.load_all()
        return sorted((s.summary() for s in self.sessions.values()), key=lambda s: -s["updatedAt"])

    def get(self, sid: str) -> Optional[Session]:
        self.load_all()
        return self.sessions.get(sid)

    def create(self, directory: str, agent: str = "build", title: str = "") -> Session:
        self.load_all()
        ts = now_ms()
        data = {
            "id": new_id("ses"),
            "title": title or "New session",
            "agent": agent,
            "directory": str(Path(directory).expanduser()),
            "createdAt": ts,
            "updatedAt": ts,
            "messages": [],
            "history": [],
            "todos": [],
            "runs": [],
            "flow": None,
            "permissionLog": [],
            "questionLog": [],
            "alwaysAllow": [],
            "goal": None,
            "autoMode": None,
            "baseSnapshot": None,
            "tokens": {"input": 0, "output": 0},
        }
        session = Session(data)
        self.sessions[session.id] = session
        session.save()
        return session

    def delete(self, sid: str) -> bool:
        session = self.sessions.pop(sid, None)
        if not session:
            return False
        if session.task and not session.task.done():
            session.task.cancel()
        for path in (SESSIONS_DIR / f"{sid}.json", SESSIONS_DIR / f"{sid}.journal.jsonl"):
            if path.exists():
                path.unlink()
        runs_dir = SESSIONS_DIR / sid
        if runs_dir.is_dir():
            for f in runs_dir.iterdir():
                f.unlink()
            runs_dir.rmdir()
        return True


def close_parts(messages: list[dict]) -> None:
    for msg in messages:
        for part in msg.get("parts", []):
            if part.get("type") == "tool" and part.get("status") in ("pending", "running", "waiting"):
                part["status"] = "error"
                part["output"] = "Interrupted (server restarted)."


def _recover_interrupted(data: dict) -> None:
    """A session saved mid-run (server stopped): close what was left running."""
    if "autonomous" in data:  # v2 key
        data["autoMode"] = True if data.pop("autonomous") else None

    close_parts(data.get("messages", []))
    for q in data.get("questionLog", []):
        if q.get("status") == "pending":
            q["status"] = "rejected"
    for run in data.get("runs", []):
        close_parts(run.get("messages", []))
        if run.get("status") == "running":
            run["status"] = "failed"
            run["endedAt"] = run.get("endedAt") or now_ms()
    for p in data.get("permissionLog", []):
        if p.get("status") == "pending":
            p["status"] = "rejected"
    flow = data.get("flow")
    if flow and not flow.get("isFinished"):
        flow["wasExecuting"] = bool(flow.get("executing"))
        flow["executing"] = False
        flow["interrupted"] = True
        flow["running"] = 0
        for t in flow.get("tasks", []):
            if t.get("status") in ("running", "review"):
                t["status"] = "planned"


store = SessionStore()
