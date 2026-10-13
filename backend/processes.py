"""Terminal and background processes, for the user and for the agents (Claude Code-style).

- The user's terminal: commands typed in the UI run in the session folder (``cd`` is tracked),
  their output streams live over SSE, input can be sent to a running program, Ctrl+C kills it.
- The agents: ``bash`` with ``run_in_background`` starts a long-running process (dev server,
  watcher, test runner); ``bash_output`` reads what it printed since the last read (optionally
  waiting for more), ``bash_kill`` stops it. Processes started by a savvy worker die with it.
- Local URLs printed by a process (``http://localhost:5173``...) are detected for the preview.

No PTY is used (portable to Windows): programs see pipes, not a terminal.
"""
from __future__ import annotations

import asyncio
import codecs
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import load_config
from .osutil import _spawn, git_safety_env, kill_tree
from .session import new_id, now_ms

MAX_BUFFER = 1_000_000          # characters kept per process (older output is dropped)
URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|(?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?(?:/[^\s'\"<>)\]]*)?")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


@dataclass
class Proc:
    id: str
    session_id: str
    command: str
    cwd: str
    owner: str                      # user | agent
    kind: str                       # terminal | background | shell
    run_id: Optional[str] = None    # savvy worker run that started it
    started_at: int = field(default_factory=now_ms)
    ended_at: Optional[int] = None
    exit_code: Optional[int] = None
    status: str = "running"         # running | exited | killed
    urls: list[str] = field(default_factory=list)
    base: int = 0                   # absolute offset of buffer[0]
    buffer: str = ""
    reads: dict = field(default_factory=dict)   # reader -> absolute offset already read
    popen: Any = None
    done: Optional[asyncio.Event] = None
    changed: Optional[asyncio.Event] = None

    @property
    def end(self) -> int:
        return self.base + len(self.buffer)

    def public(self) -> dict:
        return {"id": self.id, "command": self.command, "cwd": self.cwd, "owner": self.owner, "kind": self.kind,
                "runId": self.run_id, "startedAt": self.started_at, "endedAt": self.ended_at, "exitCode": self.exit_code,
                "status": self.status, "urls": self.urls, "size": self.end}

    def text_since(self, offset: int) -> tuple[str, int]:
        start = max(offset, self.base)
        return self.buffer[start - self.base:], self.end


class ProcessManager:
    def __init__(self) -> None:
        self.procs: dict[str, Proc] = {}
        self._lock = threading.Lock()

    # -- queries ---------------------------------------------------------------
    def for_session(self, session_id: str) -> list[Proc]:
        return [p for p in self.procs.values() if p.session_id == session_id]

    def get(self, session_id: str, pid: str) -> Optional[Proc]:
        p = self.procs.get(pid)
        return p if p and p.session_id == session_id else None

    def urls(self, session_id: str) -> list[dict]:
        out = []
        for p in self.for_session(session_id):
            if p.status == "running":
                out += [{"url": u, "procId": p.id, "command": p.command} for u in p.urls]
        return out

    # -- lifecycle ---------------------------------------------------------------
    def start(self, session, command: str, *, cwd: Optional[str] = None, owner: str = "user", kind: str = "terminal",
              run_id: Optional[str] = None) -> Proc:
        cfg = load_config()
        running = [p for p in self.for_session(session.id) if p.status == "running"]
        limit = int(cfg["terminal"].get("maxProcesses") or 8)
        if len(running) >= limit:
            raise RuntimeError(f"{len(running)} processes are already running in this session (limit {limit}): stop one first.")
        cwd = cwd or session.directory
        env = {**os.environ, "CI": "1", "GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0",
               "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0", "NO_COLOR": "1",
               **git_safety_env()}
        popen = _spawn(command, cwd, env, cfg.get("shell", "auto"), True)
        loop = asyncio.get_running_loop()
        proc = Proc(new_id("proc"), session.id, command, cwd, owner, kind, run_id, popen=popen,
                    done=asyncio.Event(), changed=asyncio.Event())
        self.procs[proc.id] = proc
        session.emit("terminal.updated", {"process": proc.public()})
        pending: list[str] = []
        state = {"scheduled": False}

        def flush() -> None:
            state["scheduled"] = False
            if not pending:
                return
            chunk = "".join(pending)
            pending.clear()
            offset = proc.end
            proc.buffer += chunk
            if len(proc.buffer) > MAX_BUFFER:
                drop = len(proc.buffer) - MAX_BUFFER
                proc.buffer = proc.buffer[drop:]
                proc.base += drop
            found = False
            for m in URL_RE.findall(strip_ansi(chunk)):
                url = m.replace("0.0.0.0", "localhost").rstrip(".,;")
                if url not in proc.urls and len(proc.urls) < 6:
                    proc.urls.append(url)
                    found = True
            proc.changed.set()
            session.emit("terminal.output", {"procId": proc.id, "offset": offset, "chunk": chunk})
            if found:
                session.emit("terminal.updated", {"process": proc.public()})

        def schedule(text: str) -> None:
            pending.append(text)
            if not state["scheduled"]:
                state["scheduled"] = True
                loop.call_later(0.06, flush)

        def finished(code: int) -> None:
            flush()
            if proc.status == "running":
                proc.status = "exited"
            proc.exit_code = code
            proc.ended_at = now_ms()
            proc.done.set()
            proc.changed.set()
            session.emit("terminal.updated", {"process": proc.public()})

        def reader() -> None:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            out = popen.stdout
            read = getattr(out, "read1", out.read)
            try:
                while True:
                    data = read(65536)
                    if not data:
                        break
                    text = decoder.decode(data)
                    if text:
                        loop.call_soon_threadsafe(schedule, text.replace("\r\n", "\n"))
            except (OSError, ValueError):
                pass
            tail = decoder.decode(b"", final=True)
            if tail:
                loop.call_soon_threadsafe(schedule, tail)
            code = popen.wait()
            try:
                loop.call_soon_threadsafe(finished, code)
            except RuntimeError:
                pass

        threading.Thread(target=reader, daemon=True).start()
        return proc

    def write(self, proc: Proc, text: str) -> None:
        if proc.status != "running" or not proc.popen.stdin:
            raise RuntimeError("the process is not running")
        data = text.encode("utf-8")
        with self._lock:
            proc.popen.stdin.write(data)
            proc.popen.stdin.flush()

    def kill(self, proc: Proc) -> None:
        if proc.status == "running":
            proc.status = "killed"
            kill_tree(proc.popen)

    def remove(self, session, pid: str) -> None:
        p = self.get(session.id, pid)
        if p:
            self.kill(p)
            self.procs.pop(pid, None)
            session.emit("terminal.removed", {"procId": pid})

    def kill_run(self, run_id: str) -> None:
        for p in list(self.procs.values()):
            if p.run_id == run_id:
                self.kill(p)

    def kill_session(self, session_id: str) -> None:
        for p in self.for_session(session_id):
            self.kill(p)
            self.procs.pop(p.id, None)

    def shutdown(self) -> None:
        for p in list(self.procs.values()):
            self.kill(p)

    async def wait(self, proc: Proc, timeout: float) -> bool:
        try:
            await asyncio.wait_for(proc.done.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def read_new(self, proc: Proc, reader: str, wait: float = 0.0, max_chars: int = 30000) -> str:
        """Output since this reader's last read; waits up to ``wait`` seconds for something new."""
        start = proc.reads.get(reader, 0)
        deadline = time.monotonic() + max(0.0, min(wait, 120.0))
        while proc.end <= start and proc.status == "running" and time.monotonic() < deadline:
            proc.changed.clear()
            try:
                await asyncio.wait_for(proc.changed.wait(), max(0.05, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
        text, end = proc.text_since(start)
        proc.reads[reader] = end
        text = strip_ansi(text)
        if len(text) > max_chars:
            text = f"... [{len(text) - max_chars} earlier characters skipped]\n" + text[-max_chars:]
        return text


manager = ProcessManager()

CD_RE = re.compile(r"^\s*cd(?:\s+(.*))?\s*$")


def terminal_cwd(session) -> str:
    cwd = session.data.get("terminalCwd")
    return cwd if cwd and Path(cwd).is_dir() else session.directory


def change_dir(session, arg: str) -> str:
    """Handle ``cd`` in the user's terminal (each command runs in its own process)."""
    arg = (arg or "").strip().strip('"').strip("'")
    current = Path(terminal_cwd(session))
    target = Path(session.directory) if not arg else Path(os.path.expanduser(arg))
    if not target.is_absolute():
        target = current / target
    target = target.resolve()
    if not target.is_dir():
        raise FileNotFoundError(f"cd: no such directory: {arg}")
    session.data["terminalCwd"] = str(target)
    session.touch()
    return str(target)
