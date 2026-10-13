"""Workspace API: the user's terminal, ``!command`` shell mode, the file explorer / editor and previews.

Every path is confined to the session folder (symlinks resolved). Files served for preview carry a
sandbox Content-Security-Policy: a page from the workspace runs in an opaque origin and cannot call
this API.
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import journal, netguard
from .config import load_config
from .osutil import posix, read_text, write_text
from .processes import CD_RE, change_dir, manager, strip_ansi, terminal_cwd
from .session import MessageSink, Session, new_id, now_ms, store
from .security import PREVIEW_CSP
from .tools import IGNORED_DIRS, open_external

router = APIRouter()
MAX_EDIT_BYTES = 2_000_000
TEXT_EXT = {".txt", ".md", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".scss", ".html", ".htm", ".yml", ".yaml",
            ".toml", ".ini", ".cfg", ".sh", ".bat", ".ps1", ".sql", ".csv", ".tsv", ".xml", ".svg", ".go", ".rs", ".java", ".kt",
            ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".vue", ".svelte", ".env", ".gitignore", ".dockerfile", ".lock"}


def _get(sid: str) -> Session:
    s = store.get(sid)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _path(s: Session, raw: str, must_exist: bool = True) -> Path:
    root = Path(s.directory).resolve()
    raw = (raw or "").strip()
    p = Path(raw)
    p = p.resolve() if p.is_absolute() else (root / raw.replace("\\", "/").lstrip("/")).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(403, "Path outside the session folder")
    if must_exist and not p.exists():
        raise HTTPException(404, f"Not found: {raw}")
    return p


def _rel(s: Session, p: Path) -> str:
    return posix(p.relative_to(Path(s.directory).resolve())) or "."


def _is_binary(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            return b"\0" in fh.read(8192)
    except OSError:
        return True


# ---------------------------------------------------------------------------
# terminal


class TerminalRun(BaseModel):
    command: str


class TerminalInput(BaseModel):
    text: str
    newline: bool = True


@router.get("/api/sessions/{sid}/terminal")
async def terminal_list(sid: str) -> dict:
    s = _get(sid)
    procs = manager.for_session(s.id)
    return {"cwd": terminal_cwd(s), "processes": [{**p.public(), "output": p.text_since(max(p.base, p.end - 200_000))[0]} for p in procs],
            "urls": manager.urls(s.id)}


@router.post("/api/sessions/{sid}/terminal/run")
async def terminal_run(sid: str, body: TerminalRun) -> dict:
    """A command typed by the user: no permission prompt (the user is the one asking)."""
    s = _get(sid)
    command = body.command.strip()
    if not command:
        raise HTTPException(400, "Empty command")
    m = CD_RE.match(command)
    if m:
        try:
            cwd = change_dir(s, m.group(1) or "")
        except FileNotFoundError as e:
            raise HTTPException(400, str(e)) from e
        s.emit("terminal.cwd", {"cwd": cwd})
        return {"cwd": cwd}
    try:
        proc = manager.start(s, command, cwd=terminal_cwd(s), owner="user", kind="terminal")
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    journal.log(s, "terminal", command=command[:500], cwd=proc.cwd, procId=proc.id)
    return {"process": proc.public(), "cwd": proc.cwd}


@router.post("/api/sessions/{sid}/terminal/{pid}/input")
async def terminal_input(sid: str, pid: str, body: TerminalInput) -> dict:
    s = _get(sid)
    p = manager.get(s.id, pid)
    if not p:
        raise HTTPException(404, "No such process")
    try:
        manager.write(p, body.text + ("\n" if body.newline else ""))
    except (RuntimeError, OSError) as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/api/sessions/{sid}/terminal/{pid}/kill")
async def terminal_kill(sid: str, pid: str) -> dict:
    s = _get(sid)
    p = manager.get(s.id, pid)
    if not p:
        raise HTTPException(404, "No such process")
    manager.kill(p)
    journal.log(s, "terminal", kill=pid, command=p.command[:200])
    return {"ok": True}


@router.delete("/api/sessions/{sid}/terminal/{pid}")
async def terminal_remove(sid: str, pid: str) -> dict:
    manager.remove(_get(sid), pid)
    return {"ok": True}


# ---------------------------------------------------------------------------
# "!command": run in the terminal and share the result with the agent


class ShellIn(BaseModel):
    command: str


@router.post("/api/sessions/{sid}/shell")
async def shell(sid: str, body: ShellIn) -> dict:
    """Claude Code's ``!`` mode: the user runs a command; command + output join the conversation as context
    for the next turn. Long-running commands keep running in the terminal."""
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "Session is busy")
    command = body.command.strip().lstrip("!").strip()
    if not command:
        raise HTTPException(400, "Empty command")
    part = {"id": new_id("prt"), "type": "shell", "command": command, "output": "", "exitCode": None, "status": "running",
            "startedAt": now_ms()}
    sink = MessageSink(s)
    msg = sink.add_message("user", s.data.get("agent", "build"), parts=[part])
    journal.log(s, "shell", command=command[:500])
    m = CD_RE.match(command)
    if m:
        try:
            cwd = change_dir(s, m.group(1) or "")
            part.update(output=f"cwd: {cwd}", exitCode=0, status="done")
        except FileNotFoundError as e:
            part.update(output=str(e), exitCode=1, status="error")
        sink.update_part(msg, part)
        return {"part": part}
    try:
        proc = manager.start(s, command, cwd=terminal_cwd(s), owner="user", kind="shell")
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    part["procId"] = proc.id
    sink.update_part(msg, part)
    timeout = float(load_config()["terminal"].get("shellTimeout") or 120)
    finished = await manager.wait(proc, timeout)
    output = strip_ansi(proc.text_since(proc.base)[0])
    part.update(output=output[-12000:], exitCode=proc.exit_code if finished else None,
                status=("done" if proc.exit_code == 0 else "error") if finished else "running", endedAt=now_ms())
    sink.update_part(msg, part)
    state = f"exit code {proc.exit_code}" if finished else f"still running after {int(timeout)}s (see the terminal)"
    context = f"I ran this command in the terminal ({state}):\n$ {command}\n```\n{output[-6000:].strip()}\n```"
    pending = s.data.get("pendingContext") or ""
    s.data["pendingContext"] = (pending + "\n\n" + context).strip()
    s.touch()
    return {"part": part}


# ---------------------------------------------------------------------------
# files: explorer, editor, previews


class FileWrite(BaseModel):
    path: str
    content: str
    mtime: Optional[float] = None  # the mtime the editor loaded; null = create a new file


class PathIn(BaseModel):
    path: str


@router.get("/api/sessions/{sid}/tree")
async def tree(sid: str, path: str = "", hidden: bool = False) -> dict:
    s = _get(sid)
    d = _path(s, path or ".")
    if not d.is_dir():
        raise HTTPException(400, "Not a folder")
    entries = []
    try:
        for e in sorted(os.scandir(d), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            if not hidden and (e.name.startswith(".") and e.name not in (".github", ".env.example", ".mcp.json", ".supercode")):
                continue
            try:
                st = e.stat(follow_symlinks=False)
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            entries.append({"name": e.name, "path": _rel(s, Path(e.path).resolve()) if not e.is_symlink() else posix(Path(_rel(s, d)) / e.name),
                            "dir": is_dir, "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
                            "ignored": e.name in IGNORED_DIRS, "symlink": e.is_symlink()})
            if len(entries) >= 2000:
                break
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    return {"path": _rel(s, d), "entries": entries}


@router.get("/api/sessions/{sid}/file")
async def read_file(sid: str, path: str) -> dict:
    s = _get(sid)
    p = _path(s, path)
    if p.is_dir():
        raise HTTPException(400, "This is a folder")
    st = p.stat()
    mime = mimetypes.guess_type(p.name)[0] or ""
    binary = _is_binary(p)
    content = None
    if not binary and st.st_size <= MAX_EDIT_BYTES:
        try:
            content = read_text(p)
        except (OSError, UnicodeDecodeError):
            binary = True
    return {"path": _rel(s, p), "size": st.st_size, "mtime": st.st_mtime, "mime": mime, "binary": binary,
            "content": content, "tooLarge": not binary and content is None, "crlf": bool(content and "\r\n" in content)}


@router.put("/api/sessions/{sid}/file")
async def write_file(sid: str, body: FileWrite) -> dict:
    """The user saves a file from the editor (no permission prompt: the user is editing)."""
    s = _get(sid)
    p = _path(s, body.path, must_exist=False)
    if p.is_dir():
        raise HTTPException(400, "This is a folder")
    if body.mtime is None and p.exists():
        raise HTTPException(409, f"{body.path} already exists")
    if body.mtime is not None and p.exists() and abs(p.stat().st_mtime - body.mtime) > 1e-6:
        raise HTTPException(409, "The file changed on disk since you opened it (the agent or another program): reload it first.")
    content = body.content
    if p.exists():
        try:
            if "\r\n" in read_text(p) and "\r\n" not in content:
                content = content.replace("\n", "\r\n")  # keep the file's line endings
        except (OSError, UnicodeDecodeError):
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, content)
    journal.log(s, "user_edit", path=_rel(s, p), bytes=len(content))
    s.emit("files.changed", {"paths": [_rel(s, p)]})
    return {"path": _rel(s, p), "mtime": p.stat().st_mtime}


@router.post("/api/sessions/{sid}/file/delete")
async def delete_file(sid: str, body: PathIn) -> dict:
    s = _get(sid)
    p = _path(s, body.path)
    if p == Path(s.directory).resolve():
        raise HTTPException(400, "Cannot delete the project folder")
    if p.is_dir():
        if any(p.iterdir()):
            raise HTTPException(400, "Only empty folders can be deleted here")
        p.rmdir()
    else:
        p.unlink()
    journal.log(s, "user_edit", path=body.path, deleted=True)
    s.emit("files.changed", {"paths": [body.path]})
    return {"ok": True}


@router.post("/api/sessions/{sid}/mkdir")
async def make_dir(sid: str, body: PathIn) -> dict:
    s = _get(sid)
    p = _path(s, body.path, must_exist=False)
    p.mkdir(parents=True, exist_ok=True)
    return {"path": _rel(s, p)}


@router.post("/api/sessions/{sid}/open-external")
async def open_with_system(sid: str, body: PathIn) -> dict:
    """The user clicked "Open with the system app" (or "Show in folder")."""
    s = _get(sid)
    target = body.path.strip()
    if target.startswith(("http://", "https://")):
        if not netguard.url_is_local(target):
            raise HTTPException(400, "Only local URLs")
        dest = target
    else:
        dest = str(_path(s, target))
    try:
        await asyncio.to_thread(open_external, dest)
    except OSError as e:
        raise HTTPException(400, f"Cannot open: {e}") from e
    return {"ok": True}


@router.get("/api/sessions/{sid}/raw/{path:path}")
async def raw(sid: str, path: str) -> FileResponse:
    """Workspace files for previews (images, PDFs, HTML with its assets), sandboxed."""
    s = _get(sid)
    p = _path(s, path)
    if p.is_dir():
        index = p / "index.html"
        if not index.is_file():
            raise HTTPException(404, "No index.html in this folder")
        p = index
    mime = mimetypes.guess_type(p.name)[0] or ("text/plain" if p.suffix.lower() in TEXT_EXT else "application/octet-stream")
    if mime.startswith("text/") or mime in ("application/javascript", "application/json", "image/svg+xml"):
        mime += "; charset=utf-8"
    return FileResponse(p, media_type=mime, headers={"Content-Security-Policy": PREVIEW_CSP, "Cache-Control": "no-store",
                                                     "X-Content-Type-Options": "nosniff"})


@router.get("/api/sessions/{sid}/preview/targets")
async def preview_targets(sid: str) -> dict:
    """What can be previewed: local URLs printed by running processes, and HTML entry points of the workspace."""
    s = _get(sid)
    root = Path(s.directory)
    pages = []
    for cand in ("index.html", "public/index.html", "dist/index.html", "build/index.html", "docs/index.html", "site/index.html"):
        if (root / cand).is_file():
            pages.append(cand)
    return {"urls": manager.urls(s.id), "pages": pages}


def cleanup_session(session_id: str) -> None:
    manager.kill_session(session_id)
