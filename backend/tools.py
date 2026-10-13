"""OpenCode-style tools.

read, list, glob, grep, write, edit, multiedit, apply_patch, bash, webfetch, repomap,
todowrite/todoread, question, notes, step, task.

Safety and robustness, after OpenCode:
- every path resolves inside the session directory (``resolve``);
- an existing file must have been read by this agent, and not changed since, before it
  is edited or overwritten (stale-write protection for parallel workers);
- writes to one file are serialized with a per-path lock;
- long outputs are cut and the full text is saved to a spill file the agent can read.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import html
import os
import re
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import editing
from .config import DATA_DIR, load_config
from .osutil import lock_key, posix, read_text, run_command, write_text

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".pytest_cache",
                ".mypy_cache", ".idea", ".vscode", "target", ".gradle", "coverage", ".turbo", ".cache"}
MAX_READ_LINES = 2000
MAX_LINE_LEN = 2000
MAX_OUTPUT = 30_000
SPILL_DIR = DATA_DIR / "tool-output"

_file_locks: dict[str, asyncio.Lock] = {}


def file_lock(p: Path) -> asyncio.Lock:
    return _file_locks.setdefault(lock_key(p), asyncio.Lock())


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    output: str
    title: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolContext:
    directory: Path
    session: Any
    agent: str
    run: Optional[dict] = None
    # path -> content before the first change (None if the file was new)
    changes: dict[str, Optional[str]] = field(default_factory=dict)
    # absolute path -> mtime_ns when this agent last read or wrote it
    read_times: dict[str, int] = field(default_factory=dict)
    require_read: bool = True
    on_step: Optional[Callable[[dict], None]] = None
    on_todos: Optional[Callable[[list], None]] = None
    delegate: Optional[Callable[[dict], Awaitable[str]]] = None
    ask: Optional[Callable[[list], Awaitable[list]]] = None
    notes: Optional[Callable[[str, str], list]] = None  # (action, text) -> notes
    repo_map_chars: int = 6000
    # Folders outside the workspace the agent may read (loaded skills and their reference files).
    extra_read_roots: list[Path] = field(default_factory=list)


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return f"{head}\n\n... [{len(text) - limit} characters truncated] ...\n\n{tail}"


def bound_output(text: str, limit: int) -> tuple[str, Optional[str]]:
    """Cut long tool output; the full text goes to a spill file the agent can `read`."""
    if len(text) <= limit:
        return text, None
    SPILL_DIR.mkdir(parents=True, exist_ok=True)
    spill = SPILL_DIR / f"tool_{uuid.uuid4().hex[:12]}.txt"
    write_text(spill, text)
    head = text[: int(limit * 0.6)]
    tail = text[-int(limit * 0.3):]
    total_lines = text.count("\n") + 1
    marker = (f"\n\n... [output cut: {len(text)} chars, {total_lines} lines. Full output saved to {spill} "
              f"- use read with offset/limit or grep on it if you need more] ...\n\n")
    return head + marker + tail, str(spill)


def resolve(ctx: ToolContext, raw: str, *, for_read: bool = False) -> Path:
    if not raw:
        raise ToolError("path is required")
    root = ctx.directory.resolve()
    p = Path(raw).expanduser()
    p = (p if p.is_absolute() else root / p).resolve()
    if p != root and root not in p.parents:
        spill = SPILL_DIR.resolve()
        if for_read and spill in p.parents:
            return p
        if for_read and any(r == p or r in p.parents for r in (x.resolve() for x in ctx.extra_read_roots)):
            return p
        raise ToolError(f"{raw} is outside the workspace {root}")
    # The app's own data (config with secrets, sessions, mods, skills) is never part of a project, even when
    # the project folder contains it - unless the agent works inside it on purpose (the mod / skill builders).
    data = DATA_DIR.resolve()
    if (p == data or data in p.parents) and not (root == data or data in root.parents):
        raise ToolError(f"{raw} is the app's private data folder, not part of the project")
    # .git internals (config, hooks) would let an edit turn into code execution: read-only for the agents.
    if not for_read and ".git" in p.relative_to(root).parts:
        raise ToolError(f"{raw} is inside .git: the agents may read it but not modify it (use git commands)")
    return p


def rel(ctx: ToolContext, p: Path) -> str:
    try:
        return posix(p.resolve().relative_to(ctx.directory.resolve())) or "."
    except ValueError:
        return posix(p)


def inside(root: Path, p: Path) -> bool:
    """True when ``p`` (symlinks resolved) is in ``root`` - walkers skip links pointing outside the workspace."""
    try:
        r = p.resolve()
    except OSError:
        return False
    return r == root or root in r.parents


def _walk(root: Path):
    real = root.resolve()
    data = DATA_DIR.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and (not d.startswith(".") or d in (".github",))
                             and (Path(dirpath) / d).resolve() != data)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() and not inside(real, p):
                continue
            yield p


def _is_text(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            return b"\0" not in fh.read(4096)
    except OSError:
        return False


def make_diff(path: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}", n=3
    )
    return "".join(l if l.endswith("\n") else l + "\n" for l in lines)


def _diff_stats(diff: str) -> dict:
    add = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    rem = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return {"additions": add, "deletions": rem}


def _record_change(ctx: ToolContext, p: Path, before: Optional[str]) -> None:
    key = rel(ctx, p)
    if key not in ctx.changes:
        ctx.changes[key] = before


def _mtime(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _mark_read(ctx: ToolContext, p: Path) -> None:
    ctx.read_times[str(p)] = _mtime(p)


def _check_fresh(ctx: ToolContext, p: Path) -> None:
    """Refuse to overwrite a file this agent has not read, or that changed since it read it."""
    if not ctx.require_read or not p.exists():
        return
    seen = ctx.read_times.get(str(p))
    if seen is None:
        raise ToolError(f"Read {rel(ctx, p)} before modifying it (the edit must be based on its current content).")
    if _mtime(p) != seen:
        raise ToolError(f"{rel(ctx, p)} was modified (by another agent or the user) since you read it. Read it again, then retry.")


# ---------------------------------------------------------------------------
# read-only tools


async def t_read(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or args.get("file_path") or "", for_read=True)
    if not p.exists():
        hint = ""
        if p.parent.exists():
            close = difflib.get_close_matches(p.name, [c.name for c in p.parent.iterdir()], n=3)
            if close:
                hint = " Did you mean: " + ", ".join(close)
        raise ToolError(f"File not found: {rel(ctx, p)}.{hint}")
    if p.is_dir():
        entries = sorted(c.name + ("/" if c.is_dir() else "") for c in p.iterdir())[:200]
        return ToolResult(f"{rel(ctx, p)} is a directory:\n" + "\n".join(entries), rel(ctx, p))
    if not _is_text(p):
        raise ToolError(f"{rel(ctx, p)} looks binary")
    lines = read_text(p).splitlines()
    offset = max(0, int(args.get("offset") or 1) - 1)
    limit = int(args.get("limit") or MAX_READ_LINES)
    chunk = lines[offset: offset + limit]
    body = "\n".join(f"{i + offset + 1:>6}\t{l[:MAX_LINE_LEN]}" for i, l in enumerate(chunk))
    more = len(lines) - (offset + len(chunk))
    if more > 0:
        body += f"\n\n(File has {more} more lines. Use offset={offset + len(chunk) + 1} to read further.)"
    _mark_read(ctx, p)
    return ToolResult(body or "(empty file)", rel(ctx, p), {"lines": len(chunk), "total": len(lines)})


async def t_list(ctx: ToolContext, args: dict) -> ToolResult:
    root = resolve(ctx, args.get("path") or ".", for_read=True)
    if not root.is_dir():
        raise ToolError(f"{rel(ctx, root)} is not a directory")
    out: list[str] = []
    limit = 400
    for f in _walk(root):
        out.append(rel(ctx, f))
        if len(out) >= limit:
            out.append(f"... (truncated at {limit} entries; use glob to narrow)")
            break
    return ToolResult("\n".join(out) or "(empty directory)", rel(ctx, root), {"count": len(out)})


async def t_glob(ctx: ToolContext, args: dict) -> ToolResult:
    pattern = args.get("pattern") or "*"
    root = resolve(ctx, args.get("path") or ".", for_read=True)
    matches = []
    for f in _walk(root):
        r = posix(f.relative_to(root))
        if fnmatch.fnmatch(r, pattern) or fnmatch.fnmatch(f.name, pattern) or Path(r).match(pattern):
            matches.append((f.stat().st_mtime if f.exists() else 0, rel(ctx, f)))
        if len(matches) >= 500:
            break
    matches.sort(key=lambda x: -x[0])  # most recently modified first, like OpenCode
    shown = [m[1] for m in matches[:200]]
    return ToolResult("\n".join(shown) or "No files found", pattern, {"count": len(shown)})


async def t_grep(ctx: ToolContext, args: dict) -> ToolResult:
    pattern = args.get("pattern") or ""
    try:
        rx = re.compile(pattern, re.I if args.get("ignore_case") else 0)
    except re.error as e:
        raise ToolError(f"Invalid regex: {e}") from e
    root = resolve(ctx, args.get("path") or ".", for_read=True)
    include = args.get("include") or ""
    hits: list[str] = []
    files = [root] if root.is_file() else _walk(root)
    for f in files:
        if include and not (fnmatch.fnmatch(f.name, include) or Path(f).match(include)):
            continue
        if not _is_text(f):
            continue
        try:
            for n, line in enumerate(read_text(f).splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel(ctx, f)}:{n}: {line.strip()[:300]}")
                    if len(hits) >= 200:
                        break
        except OSError:
            continue
        if len(hits) >= 200:
            hits.append("... (truncated at 200 matches; narrow the pattern or path)")
            break
    return ToolResult("\n".join(hits) or "No matches", pattern, {"count": len(hits)})


async def t_repomap(ctx: ToolContext, args: dict) -> ToolResult:
    from . import repomap  # local import: repomap imports this module

    text = await asyncio.to_thread(repomap.build, str(ctx.directory), int(args.get("chars") or ctx.repo_map_chars),
                                   str(args.get("focus") or ""))
    return ToolResult(text or "(no source files found)", args.get("focus") or "repo map", {"chars": len(text)})


# ---------------------------------------------------------------------------
# write tools


async def t_write(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or args.get("file_path") or "")
    content = args.get("content")
    if content is None:
        raise ToolError("content is required")
    async with file_lock(p):
        _check_fresh(ctx, p)
        before = read_text(p) if p.exists() else None
        if before and "\r\n" in before and "\r\n" not in content:
            content = content.replace("\n", "\r\n")  # keep the file's CRLF style (Windows files)
        _record_change(ctx, p, before)
        p.parent.mkdir(parents=True, exist_ok=True)
        write_text(p, content)
        _mark_read(ctx, p)
    diff = make_diff(rel(ctx, p), before or "", content)
    verb = "Updated" if before is not None else "Created"
    return ToolResult(f"{verb} {rel(ctx, p)} ({len(content.splitlines())} lines)", rel(ctx, p), {"diff": diff, "files": [rel(ctx, p)], **_diff_stats(diff)})


async def _edit_file(ctx: ToolContext, p: Path, edits: list[dict]) -> ToolResult:
    async with file_lock(p):
        if not p.exists():
            first = edits[0]
            if len(edits) == 1 and not first.get("old_string"):
                p.parent.mkdir(parents=True, exist_ok=True)
                _record_change(ctx, p, None)
                write_text(p, first.get("new_string") or "")
                _mark_read(ctx, p)
                diff = make_diff(rel(ctx, p), "", first.get("new_string") or "")
                return ToolResult(f"Created {rel(ctx, p)}", rel(ctx, p), {"diff": diff, "files": [rel(ctx, p)], **_diff_stats(diff)})
            raise ToolError(f"File not found: {rel(ctx, p)}. Use write to create it.")
        _check_fresh(ctx, p)
        before = read_text(p)
        after = before
        strategies = []
        for i, e in enumerate(edits, 1):
            old = e.get("old_string", e.get("oldString"))
            new = e.get("new_string", e.get("newString"))
            if old is None or new is None:
                raise ToolError(f"edit {i}: old_string and new_string are required")
            try:
                after, _, strategy = editing.apply_edit(after, old, new, bool(e.get("replace_all", e.get("replaceAll"))))
            except editing.EditError as err:
                prefix = f"edit {i} of {len(edits)}: " if len(edits) > 1 else ""
                raise ToolError(f"{prefix}{err} No change was written.") from err
            strategies.append(strategy)
        _record_change(ctx, p, before)
        write_text(p, after)
        _mark_read(ctx, p)
    diff = make_diff(rel(ctx, p), before, after)
    loose = [s for s in strategies if s != "exact"]
    note = f" (matched with {', '.join(sorted(set(loose)))})" if loose else ""
    return ToolResult(f"Edited {rel(ctx, p)}: {len(edits)} change(s){note}", rel(ctx, p),
                      {"diff": diff, "files": [rel(ctx, p)], **_diff_stats(diff), "strategies": strategies})


async def t_edit(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or args.get("file_path") or "")
    return await _edit_file(ctx, p, [args])


async def t_multiedit(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or args.get("file_path") or "")
    edits = args.get("edits") or []
    if not isinstance(edits, list) or not edits:
        raise ToolError("edits must be a non-empty list of {old_string, new_string}")
    return await _edit_file(ctx, p, edits)


async def t_apply_patch(ctx: ToolContext, args: dict) -> ToolResult:
    text = args.get("patch") or args.get("patchText") or args.get("input") or ""
    try:
        ops = editing.parse_patch(text)
    except editing.EditError as e:
        raise ToolError(f"{e}\nFormat:\n{editing.PATCH_HELP}") from e
    # Validate and compute everything first: all-or-nothing.
    planned: list[tuple[str, Path, Optional[Path], Optional[str], Optional[str]]] = []
    paths = []
    for op in ops:
        p = resolve(ctx, op.path)
        dest = resolve(ctx, op.move_to) if op.move_to else None
        paths += [p] + ([dest] if dest else [])
        if op.kind == "add":
            if p.exists():
                raise ToolError(f"Add File: {op.path} already exists (use Update File).")
            planned.append(("add", p, None, None, "\n".join(op.lines or []) + "\n"))
        elif op.kind == "delete":
            if not p.exists():
                raise ToolError(f"Delete File: {op.path} does not exist.")
            _check_fresh(ctx, p)
            planned.append(("delete", p, None, read_text(p), None))
        else:
            if not p.exists():
                raise ToolError(f"Update File: {op.path} does not exist.")
            _check_fresh(ctx, p)
            before = read_text(p)
            try:
                after = editing.apply_update(before, op.lines or [])
            except editing.EditError as e:
                raise ToolError(f"{op.path}: {e} No file was changed.") from e
            planned.append(("update", p, dest, before, after))
    locks = [file_lock(p) for p in sorted(set(paths), key=str)]
    for lk in locks:
        await lk.acquire()
    try:
        diffs, summary = [], []
        for kind, p, dest, before, after in planned:
            if kind == "add":
                _record_change(ctx, p, None)
                p.parent.mkdir(parents=True, exist_ok=True)
                write_text(p, after or "")
                _mark_read(ctx, p)
                diffs.append(make_diff(rel(ctx, p), "", after or ""))
                summary.append(f"A {rel(ctx, p)}")
            elif kind == "delete":
                _record_change(ctx, p, before)
                p.unlink()
                diffs.append(make_diff(rel(ctx, p), before or "", ""))
                summary.append(f"D {rel(ctx, p)}")
            else:
                _record_change(ctx, p, before)
                target = dest or p
                if dest:
                    _record_change(ctx, dest, read_text(dest) if dest.exists() else None)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    p.unlink()
                write_text(target, after or "")
                _mark_read(ctx, target)
                diffs.append(make_diff(rel(ctx, target), before or "", after or ""))
                summary.append(f"M {rel(ctx, p)}" + (f" -> {rel(ctx, dest)}" if dest else ""))
    finally:
        for lk in locks:
            lk.release()
    diff = "".join(diffs)
    files = [rel(ctx, dest or p) for kind, p, dest, _b, _a in planned if kind != "delete"]
    return ToolResult("Patch applied:\n" + "\n".join(summary), f"{len(planned)} file(s)",
                      {"diff": diff, "files": files, **_diff_stats(diff)})


def _reader(ctx: ToolContext) -> str:
    return f"agent:{ctx.agent}:{(ctx.run or {}).get('id', '')}"


async def t_bash(ctx: ToolContext, args: dict) -> ToolResult:
    command = args.get("command") or ""
    if not command.strip():
        raise ToolError("command is required")
    if args.get("run_in_background"):
        from .processes import manager

        try:
            proc = manager.start(ctx.session, command, cwd=str(ctx.directory), owner="agent", kind="background",
                                 run_id=(ctx.run or {}).get("id"))
        except RuntimeError as e:
            raise ToolError(str(e)) from e
        await manager.wait(proc, 3.0)  # early output or an immediate failure
        out = await manager.read_new(proc, _reader(ctx))
        state = f"exited with code {proc.exit_code}" if proc.status != "running" else "running"
        urls = f"\nLocal URLs: {', '.join(proc.urls)} (use the show tool to preview one)" if proc.urls else ""
        return ToolResult(f"Background process {proc.id} {state}.{urls}\nFirst output:\n{out.strip() or '(none yet)'}\n\n"
                          f"Read more with bash_output(id=\"{proc.id}\"), stop it with bash_kill(id=\"{proc.id}\").",
                          f"{command[:60]} (background)", {"procId": proc.id, "background": True, "urls": proc.urls})
    timeout = min(1800, max(1, int(args.get("timeout") or 120)))
    code, text, timed_out = await run_command(command, str(ctx.directory), timeout, shell=load_config().get("shell", "auto"))
    if timed_out:
        text += (f"\nCommand timed out after {timeout}s. For servers or watchers use run_in_background=true, "
                 "then bash_output to read their logs.")
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r\n", "\n").rstrip()  # fewer tokens
    return ToolResult(f"{text or '(no output)'}\n\n[exit code {code}]", command[:80], {"exitCode": code})


async def t_bash_output(ctx: ToolContext, args: dict) -> ToolResult:
    from .processes import manager

    pid = str(args.get("id") or "").strip()
    proc = manager.get(ctx.session.id, pid) if ctx.session is not None else None
    if not proc:
        running = [p.id + " " + p.command[:40] for p in manager.for_session(ctx.session.id)] if ctx.session is not None else []
        raise ToolError(f"No process {pid}. Processes: {'; '.join(running) or 'none'}")
    wait = float(args.get("wait") or 0)
    out = await manager.read_new(proc, _reader(ctx), wait=wait)
    if args.get("filter"):
        try:
            rx = re.compile(str(args["filter"]))
            out = "\n".join(l for l in out.splitlines() if rx.search(l))
        except re.error as e:
            raise ToolError(f"invalid filter regex: {e}") from e
    state = "running" if proc.status == "running" else f"{proc.status}, exit code {proc.exit_code}"
    urls = f"\nLocal URLs: {', '.join(proc.urls)}" if proc.urls else ""
    return ToolResult(f"[{proc.id} {state}]{urls}\n{out.strip() or '(no new output)'}", f"{proc.id} {state}",
                      {"procId": proc.id, "status": proc.status, "exitCode": proc.exit_code})


async def t_bash_kill(ctx: ToolContext, args: dict) -> ToolResult:
    from .processes import manager

    pid = str(args.get("id") or "").strip()
    proc = manager.get(ctx.session.id, pid) if ctx.session is not None else None
    if not proc:
        raise ToolError(f"No process {pid}")
    if proc.owner != "agent":
        raise ToolError("This process was started by the user from the terminal: ask the user to stop it.")
    manager.kill(proc)
    await manager.wait(proc, 5)
    return ToolResult(f"Stopped {proc.id} ({proc.command[:80]}).", f"stopped {proc.id}", {"procId": proc.id})


async def t_show(ctx: ToolContext, args: dict) -> ToolResult:
    """Open a file (at a line) or a local URL in the app for the user: editor, preview or data view."""
    session = ctx.session
    url = str(args.get("url") or "").strip()
    if url:
        from . import netguard

        if not netguard.url_is_local(url):
            raise ToolError("Only local URLs (localhost, private network) can be shown in the preview.")
        if session is not None:
            session.emit("ui.show", {"kind": "url", "url": url, "agent": ctx.agent})
        return ToolResult(f"Showing {url} in the preview panel.", url, {"url": url})
    p = resolve(ctx, str(args.get("path") or ""), for_read=True)
    if not p.exists():
        raise ToolError(f"Not found: {rel(ctx, p)}")
    line = int(args.get("line") or 0) or None
    if session is not None:
        session.emit("ui.show", {"kind": "file", "path": rel(ctx, p), "line": line, "preview": bool(args.get("preview")),
                                 "agent": ctx.agent})
    return ToolResult(f"Opened {rel(ctx, p)}{f' at line {line}' if line else ''} for the user.", rel(ctx, p), {"path": rel(ctx, p)})


def open_external(target: str) -> None:
    """Open a file, folder or URL with the system's default application."""
    import subprocess
    import sys

    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - user-approved
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


async def t_open(ctx: ToolContext, args: dict) -> ToolResult:
    target = str(args.get("target") or args.get("path") or args.get("url") or "").strip()
    if not target:
        raise ToolError("target is required (a workspace path or a URL)")
    if re.match(r"^https?://", target):
        from . import netguard

        if netguard.local_only() and not netguard.url_is_local(target):
            raise ToolError("Local-only mode: only local URLs can be opened.")
        dest = target
    else:
        p = resolve(ctx, target, for_read=True)
        if not p.exists():
            raise ToolError(f"Not found: {rel(ctx, p)}")
        if p.is_file() and p.suffix.lower() in (".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".sh", ".app", ".scr", ".vbs", ".jar"):
            raise ToolError("Opening executables is not allowed: run them with bash instead (it goes through the shell rules).")
        dest = str(p)
    try:
        await asyncio.to_thread(open_external, dest)
    except OSError as e:
        raise ToolError(f"Cannot open {target}: {e}") from e
    return ToolResult(f"Opened {target} with the default application.", target[:80], {"target": target})


# ---------------------------------------------------------------------------
# web


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    text = html.unescape("".join(parser.out))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def t_webfetch(ctx: ToolContext, args: dict) -> ToolResult:
    import httpx

    from . import netguard

    url = str(args.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise ToolError("url must start with http:// or https://")
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if netguard.host_is_local(parts.hostname or "") and str(parts.port or "") == os.environ.get("ORCH_BIND_PORT", "4001"):
        raise ToolError("webfetch cannot call DOINg.AI SuperCode's own API.")
    try:
        async with netguard.client(timeout=30, follow_redirects=True, max_redirects=5) as client:
            res = await client.get(url, headers={"User-Agent": "DOINg.AI-SuperCode/1.0"})
    except netguard.NetworkBlocked as e:
        raise ToolError(str(e)) from e
    except httpx.HTTPError as e:
        raise ToolError(f"Fetch failed: {e}") from e
    ctype = res.headers.get("content-type", "")
    body = res.text
    if "html" in ctype:
        body = html_to_text(body)
    return ToolResult(f"HTTP {res.status_code} {url}\n\n{body}", url, {"status": res.status_code})


# ---------------------------------------------------------------------------
# session / coordination


async def t_todowrite(ctx: ToolContext, args: dict) -> ToolResult:
    todos = args.get("todos") or []
    clean = []
    for i, t in enumerate(todos):
        if isinstance(t, str):
            t = {"content": t}
        status = t.get("status", "pending")
        clean.append({
            "id": str(t.get("id") or i + 1),
            "content": str(t.get("content", "")).strip(),
            "status": status if status in ("pending", "in_progress", "completed", "cancelled") else "pending",
        })
    if ctx.on_todos:
        ctx.on_todos(clean)
    remaining = sum(1 for t in clean if t["status"] not in ("completed", "cancelled"))
    return ToolResult(f"Todo list updated: {remaining} open of {len(clean)}", f"{len(clean)} todos", {"todos": clean})


async def t_todoread(ctx: ToolContext, args: dict) -> ToolResult:
    todos = ctx.session.data.get("todos", [])
    lines = [f"[{t['status']}] {t['content']}" for t in todos]
    return ToolResult("\n".join(lines) or "No todos", "todos")


async def t_question(ctx: ToolContext, args: dict) -> ToolResult:
    if not ctx.ask:
        raise ToolError("Questions are not available in this mode: make a reasonable assumption and state it in your answer.")
    qs = args.get("questions") or ([{"question": args["question"], "options": args.get("options") or []}] if args.get("question") else [])
    if not qs:
        raise ToolError("questions is required")
    clean = []
    for q in qs[:6]:
        if isinstance(q, str):
            q = {"question": q}
        clean.append({"question": str(q.get("question", "")).strip(), "options": [str(o) for o in (q.get("options") or [])][:8],
                      "multiple": bool(q.get("multiple"))})
    answers = await ctx.ask(clean)
    lines = [f'"{q["question"]}" = "{", ".join(a) if a else "Unanswered"}"' for q, a in zip(clean, answers)]
    return ToolResult("User answers:\n" + "\n".join(lines), f"{len(clean)} question(s)", {"answers": answers})


async def t_notes(ctx: ToolContext, args: dict) -> ToolResult:
    if not ctx.notes:
        raise ToolError("Shared notes are only available inside a savvy-flow.")
    action = str(args.get("action") or "read")
    notes = ctx.notes(action, str(args.get("text") or "").strip())
    if action == "add":
        return ToolResult("Note shared with the other workers.", "add")
    body = "\n".join(f"- [{n['author']}] {n['text']}" for n in notes[-40:])
    return ToolResult(body or "No shared notes yet.", "read", {"count": len(notes)})


async def t_step(ctx: ToolContext, args: dict) -> ToolResult:
    info = {
        "stepDone": max(0, int(args.get("done") or 0)),
        "stepTotal": max(0, int(args.get("total") or 0)) or None,
        "stepNote": str(args.get("note") or "")[:80],
    }
    if ctx.on_step:
        ctx.on_step(info)
    return ToolResult("ok", f"{info['stepDone']}/{info['stepTotal'] or '?'} {info['stepNote']}".strip())


async def t_task(ctx: ToolContext, args: dict) -> ToolResult:
    if not ctx.delegate:
        raise ToolError("task delegation is not available here")
    report = await ctx.delegate(args)
    return ToolResult(report, args.get("description") or "task")


# ---------------------------------------------------------------------------
# Registry

ToolFn = Callable[[ToolContext, dict], Awaitable[ToolResult]]


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    run: ToolFn
    permission: Optional[str] = None  # "edit" | "bash" | "webfetch" | "mcp" | "mods" | None
    mutates: bool = False
    # Extension data: MCP server / tool, read-only hint, owning mod or skill.
    meta: dict = field(default_factory=dict)
    # Category computed from the arguments (e.g. a SQL write vs a read), overriding ``permission``.
    dynamic_permission: Optional[Callable[[dict], Optional[str]]] = None

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
I = {"type": "integer"}
EDIT_ITEM = _obj({"old_string": S, "new_string": S, "replace_all": {"type": "boolean"}}, ["old_string", "new_string"])

TOOLS: dict[str, ToolDef] = {
    t.name: t
    for t in [
        ToolDef("read", "Read a text file (numbered lines). Use offset/limit for long files. Read a file before editing it.",
                _obj({"path": {**S, "description": "File path relative to the workspace"}, "offset": I, "limit": I}, ["path"]), t_read),
        ToolDef("list", "List files recursively under a directory (ignores .git, node_modules, build output).",
                _obj({"path": {**S, "description": "Directory, default '.'"}}, []), t_list),
        ToolDef("glob", "Find files by glob pattern, e.g. '**/*.py' or 'src/*.tsx'. Most recently modified first.",
                _obj({"pattern": S, "path": S}, ["pattern"]), t_glob),
        ToolDef("grep", "Search file contents with a regular expression. Returns file:line: text.",
                _obj({"pattern": S, "path": S, "include": {**S, "description": "File glob filter, e.g. '*.ts'"}, "ignore_case": {"type": "boolean"}}, ["pattern"]), t_grep),
        ToolDef("repomap", "Map of the codebase: the most important files and their function/class signatures. Cheaper than many list/read calls. Optional focus: a path fragment or symbol.",
                _obj({"focus": S, "chars": I}, []), t_repomap),
        ToolDef("write", "Create a file, or overwrite one you have read, with the full content.",
                _obj({"path": S, "content": S}, ["path", "content"]), t_write, "edit", True),
        ToolDef("edit", "Replace text in a file you have read. old_string must match exactly once (copy it from read, without line numbers) unless replace_all is true.",
                _obj({"path": S, "old_string": S, "new_string": S, "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"]), t_edit, "edit", True),
        ToolDef("multiedit", "Apply several edits to one file atomically (all or none), in order. Prefer it over several edit calls.",
                _obj({"path": S, "edits": {"type": "array", "items": EDIT_ITEM}}, ["path", "edits"]), t_multiedit, "edit", True),
        ToolDef("apply_patch", "Create, update, move or delete several files in one call. Format:\n" + editing.PATCH_HELP,
                _obj({"patch": S}, ["patch"]), t_apply_patch, "edit", True),
        ToolDef("bash", "Run a shell command in the workspace (build, tests, git...). Returns output and exit code. For servers, watchers "
                "and anything long-running set run_in_background=true, then read its output with bash_output.",
                _obj({"command": S, "timeout": {**I, "description": "Seconds, default 120"},
                      "run_in_background": {"type": "boolean", "description": "Start and return at once (dev servers, watchers)"}}, ["command"]),
                t_bash, "bash", True),
        ToolDef("bash_output", "Read the new output of a background process started with bash run_in_background (status, exit code, local URLs). "
                "wait: seconds to wait for new output (e.g. until a server is ready). filter: regex keeping matching lines.",
                _obj({"id": S, "wait": I, "filter": S}, ["id"]), t_bash_output),
        ToolDef("bash_kill", "Stop a background process you started.", _obj({"id": S}, ["id"]), t_bash_kill),
        ToolDef("show", "Show the user something in the app: a workspace file in the editor (path, optional line; preview=true renders "
                "HTML / Markdown / images / CSV), or a local URL in the preview panel (url, e.g. the dev server you started).",
                _obj({"path": S, "line": I, "preview": {"type": "boolean"}, "url": S}, []), t_show),
        ToolDef("open", "Open a workspace file or folder, or a local URL, with the system's default application (e.g. a spreadsheet in Excel, "
                "a page in the browser). Executables are refused.", _obj({"target": S}, ["target"]), t_open, "open"),
        ToolDef("webfetch", "Fetch a URL (documentation, API reference) and return it as text.",
                _obj({"url": S}, ["url"]), t_webfetch, "webfetch"),
        ToolDef("todowrite", "Replace the session todo list. Use it to plan multi-step work and mark progress.",
                _obj({"todos": {"type": "array", "items": _obj({"content": S, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}}, ["content", "status"])}}, ["todos"]), t_todowrite),
        ToolDef("todoread", "Read the session todo list.", _obj({}, []), t_todoread),
        ToolDef("question", "Ask the user one or more questions (with optional choices) when a decision is genuinely theirs. Waits for the answer.",
                _obj({"questions": {"type": "array", "items": _obj({"question": S, "options": {"type": "array", "items": S}, "multiple": {"type": "boolean"}}, ["question"])}}, ["questions"]), t_question),
        ToolDef("notes", "Shared notes between the workers of this flow. action=add to record an interface, decision or convention other tasks must know (one line); action=read to see them.",
                _obj({"action": {"type": "string", "enum": ["add", "read"]}, "text": S}, ["action"]), t_notes),
        ToolDef("step", "Report your progress to the agents panel: done steps out of total, with a short note.",
                _obj({"done": I, "total": I, "note": S}, ["done"]), t_step),
        ToolDef("task", "Delegate a well-scoped task to a savvy worker subagent. tier: fable (hardest logic/bugs), heavy (unfamiliar code, unclear cause), careful (delicate, many edge cases), medium (standard feature), light (mechanical edits, builds). The worker cannot see this chat: the prompt must be a self-contained brief.",
                _obj({"description": {**S, "description": "3-6 word title"}, "prompt": S, "tier": {"type": "string", "enum": ["fable", "heavy", "careful", "medium", "light"]}}, ["description", "prompt", "tier"]), t_task),
    ]
}

READ_ONLY = ["read", "list", "glob", "grep", "repomap"]
EDIT_TOOLS = ["write", "edit", "multiedit", "apply_patch"]
