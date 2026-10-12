"""OpenCode-style tools: read, list, glob, grep, write, edit, bash, todo, step, task.

Every path is resolved inside the session directory; a tool cannot reach outside it.
Tools return a ``ToolResult``: ``output`` goes back to the model, ``metadata`` feeds the
UI (diffs, line counts, exit codes).
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".pytest_cache", ".mypy_cache", ".idea", ".vscode"}
MAX_READ_LINES = 2000
MAX_LINE_LEN = 2000
MAX_OUTPUT = 30_000


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
    # path -> (content before the first change, or None if the file was new)
    changes: dict[str, Optional[str]] = field(default_factory=dict)
    on_step: Optional[Callable[[dict], None]] = None
    on_todos: Optional[Callable[[list], None]] = None
    delegate: Optional[Callable[[dict], Awaitable[str]]] = None


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n... [{len(text) - limit} characters truncated] ...\n\n{tail}"


def resolve(ctx: ToolContext, raw: str) -> Path:
    if not raw:
        raise ToolError("path is required")
    root = ctx.directory.resolve()
    p = Path(raw).expanduser()
    p = (p if p.is_absolute() else root / p).resolve()
    if p != root and root not in p.parents:
        raise ToolError(f"{raw} is outside the workspace {root}")
    return p


def rel(ctx: ToolContext, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ctx.directory.resolve())) or "."
    except ValueError:
        return str(p)


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".") or d in (".github", ".claude"))
        for name in sorted(filenames):
            yield Path(dirpath) / name


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


# ---------------------------------------------------------------------------
# Tool implementations


async def t_read(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or "")
    if not p.exists():
        parent = p.parent
        hint = ""
        if parent.exists():
            close = difflib.get_close_matches(p.name, [c.name for c in parent.iterdir()], n=3)
            if close:
                hint = " Did you mean: " + ", ".join(close)
        raise ToolError(f"File not found: {rel(ctx, p)}.{hint}")
    if p.is_dir():
        raise ToolError(f"{rel(ctx, p)} is a directory; use list")
    if not _is_text(p):
        raise ToolError(f"{rel(ctx, p)} looks binary")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    offset = max(0, int(args.get("offset") or 1) - 1)
    limit = int(args.get("limit") or MAX_READ_LINES)
    chunk = lines[offset : offset + limit]
    body = "\n".join(f"{i + offset + 1:>6}\t{l[:MAX_LINE_LEN]}" for i, l in enumerate(chunk))
    more = len(lines) - (offset + len(chunk))
    if more > 0:
        body += f"\n\n(File has {more} more lines. Use offset={offset + len(chunk) + 1} to read further.)"
    return ToolResult(body or "(empty file)", rel(ctx, p), {"lines": len(chunk), "total": len(lines)})


async def t_list(ctx: ToolContext, args: dict) -> ToolResult:
    root = resolve(ctx, args.get("path") or ".")
    if not root.is_dir():
        raise ToolError(f"{rel(ctx, root)} is not a directory")
    out: list[str] = []
    limit = 400
    for f in _walk(root):
        out.append(rel(ctx, f))
        if len(out) >= limit:
            out.append(f"... (truncated at {limit} entries)")
            break
    return ToolResult("\n".join(out) or "(empty directory)", rel(ctx, root), {"count": len(out)})


async def t_glob(ctx: ToolContext, args: dict) -> ToolResult:
    pattern = args.get("pattern") or "*"
    root = resolve(ctx, args.get("path") or ".")
    matches = []
    for f in _walk(root):
        r = str(f.relative_to(root))
        if fnmatch.fnmatch(r, pattern) or fnmatch.fnmatch(f.name, pattern) or Path(r).match(pattern):
            matches.append(rel(ctx, f))
        if len(matches) >= 200:
            break
    return ToolResult("\n".join(matches) or "No files found", pattern, {"count": len(matches)})


async def t_grep(ctx: ToolContext, args: dict) -> ToolResult:
    pattern = args.get("pattern") or ""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError(f"Invalid regex: {e}") from e
    root = resolve(ctx, args.get("path") or ".")
    include = args.get("include") or ""
    hits: list[str] = []
    files = [root] if root.is_file() else _walk(root)
    for f in files:
        if include and not (fnmatch.fnmatch(f.name, include) or Path(f).match(include)):
            continue
        if not _is_text(f):
            continue
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel(ctx, f)}:{n}: {line.strip()[:300]}")
                    if len(hits) >= 200:
                        break
        except OSError:
            continue
        if len(hits) >= 200:
            hits.append("... (truncated at 200 matches)")
            break
    return ToolResult("\n".join(hits) or "No matches", pattern, {"count": len(hits)})


async def t_write(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or "")
    content = args.get("content")
    if content is None:
        raise ToolError("content is required")
    before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    _record_change(ctx, p, before)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    diff = make_diff(rel(ctx, p), before or "", content)
    verb = "Updated" if before is not None else "Created"
    return ToolResult(f"{verb} {rel(ctx, p)} ({len(content.splitlines())} lines)", rel(ctx, p), {"diff": diff, **_diff_stats(diff)})


async def t_edit(ctx: ToolContext, args: dict) -> ToolResult:
    p = resolve(ctx, args.get("path") or args.get("filePath") or "")
    old = args.get("old_string", args.get("oldString"))
    new = args.get("new_string", args.get("newString"))
    if old is None or new is None:
        raise ToolError("old_string and new_string are required")
    if old == new:
        raise ToolError("old_string and new_string are identical")
    if not p.exists():
        if old == "":
            return await t_write(ctx, {"path": str(p), "content": new})
        raise ToolError(f"File not found: {rel(ctx, p)}")
    before = p.read_text(encoding="utf-8", errors="replace")
    count = before.count(old)
    if count == 0:
        # Tolerate whitespace drift at line ends, a common local-model slip.
        stripped = "\n".join(l.rstrip() for l in old.splitlines())
        norm_before = "\n".join(l.rstrip() for l in before.splitlines())
        if stripped and norm_before.count(stripped) == 1:
            after = norm_before.replace(stripped, new, 1)
            if before.endswith("\n") and not after.endswith("\n"):
                after += "\n"
        else:
            raise ToolError("old_string not found in file. Read the file again and copy the exact text.")
    elif count > 1 and not args.get("replace_all", args.get("replaceAll")):
        raise ToolError(f"old_string matches {count} places; add more context or set replace_all")
    else:
        after = before.replace(old, new) if args.get("replace_all", args.get("replaceAll")) else before.replace(old, new, 1)
    _record_change(ctx, p, before)
    p.write_text(after, encoding="utf-8")
    diff = make_diff(rel(ctx, p), before, after)
    return ToolResult(f"Edited {rel(ctx, p)}", rel(ctx, p), {"diff": diff, **_diff_stats(diff)})


async def t_bash(ctx: ToolContext, args: dict) -> ToolResult:
    command = args.get("command") or ""
    if not command.strip():
        raise ToolError("command is required")
    timeout = min(600, max(1, int(args.get("timeout") or 120)))
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(ctx.directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "CI": "1", "GIT_PAGER": "cat", "PAGER": "cat"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        code = proc.returncode
        text = out.decode("utf-8", "replace")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        code = -1
        text = f"Command timed out after {timeout}s"
    except asyncio.CancelledError:
        proc.kill()
        raise
    body = _clip(text.rstrip()) or "(no output)"
    return ToolResult(f"{body}\n\n[exit code {code}]", command[:80], {"exitCode": code})


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
    permission: Optional[str] = None  # "edit" | "bash" | None

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
I = {"type": "integer"}

TOOLS: dict[str, ToolDef] = {
    t.name: t
    for t in [
        ToolDef("read", "Read a text file. Returns numbered lines. Use offset/limit for long files.",
                _obj({"path": {**S, "description": "File path relative to the workspace"}, "offset": I, "limit": I}, ["path"]), t_read),
        ToolDef("list", "List files recursively under a directory (ignores .git, node_modules, build output).",
                _obj({"path": {**S, "description": "Directory, default '.'"}}, []), t_list),
        ToolDef("glob", "Find files by glob pattern, e.g. '**/*.py' or 'src/*.tsx'.",
                _obj({"pattern": S, "path": S}, ["pattern"]), t_glob),
        ToolDef("grep", "Search file contents with a regular expression. Returns file:line: text.",
                _obj({"pattern": S, "path": S, "include": {**S, "description": "File glob filter, e.g. '*.ts'"}}, ["pattern"]), t_grep),
        ToolDef("write", "Create or overwrite a file with the full content.",
                _obj({"path": S, "content": S}, ["path", "content"]), t_write, "edit"),
        ToolDef("edit", "Replace an exact string in a file. old_string must match exactly once unless replace_all is true. Read the file first.",
                _obj({"path": S, "old_string": S, "new_string": S, "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"]), t_edit, "edit"),
        ToolDef("bash", "Run a shell command in the workspace (build, tests, git...). Returns output and exit code.",
                _obj({"command": S, "timeout": {**I, "description": "Seconds, default 120"}}, ["command"]), t_bash, "bash"),
        ToolDef("todowrite", "Replace the session todo list. Use it to plan multi-step work and mark progress.",
                _obj({"todos": {"type": "array", "items": _obj({"content": S, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}}, ["content", "status"])}}, ["todos"]), t_todowrite),
        ToolDef("todoread", "Read the session todo list.", _obj({}, []), t_todoread),
        ToolDef("step", "Report your progress to the agents panel: done steps out of total, with a short note.",
                _obj({"done": I, "total": I, "note": S}, ["done"]), t_step),
        ToolDef("task", "Delegate a well-scoped task to a savvy worker subagent. tier: fable (hardest logic/bugs), heavy (unfamiliar code, unclear cause), careful (delicate, many edge cases), medium (standard feature), light (mechanical edits, builds). The worker cannot see this chat: the prompt must be a self-contained brief.",
                _obj({"description": {**S, "description": "3-6 word title"}, "prompt": S, "tier": {"type": "string", "enum": ["fable", "heavy", "careful", "medium", "light"]}}, ["description", "prompt", "tier"]), t_task),
    ]
}

READ_ONLY = ["read", "list", "glob", "grep"]
