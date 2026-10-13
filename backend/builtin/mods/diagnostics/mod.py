"""Check edited files and give the problems back to the agent (OpenCode-style LSP diagnostics)."""
import ast
import json
import os
import re

EDIT_TOOLS = ["write", "edit", "multiedit", "apply_patch"]
MAX_CHARS = 3000


def _python(ctx, path):
    try:
        ast.parse(ctx.read(path), filename=path)
    except SyntaxError as e:
        return [f"{path}:{e.lineno}:{e.offset or 0}: SyntaxError: {e.msg}"]
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    return []


def _json(ctx, path):
    try:
        json.loads(ctx.read(path))
    except ValueError as e:
        return [f"{path}: invalid JSON: {e}"]
    except OSError:
        pass
    return []


async def _ruff(ctx, path):
    if not ctx.which("ruff"):
        return []
    code, out = await ctx.exec(["ruff", "check", "--output-format", "concise", "--select", "E9,F63,F7,F82", "--no-cache", "--", path], timeout=30)
    return [l for l in out.splitlines() if re.match(r"^.+:\d+:\d+:", l)] if code != 0 else []


async def _node(ctx, path):
    if not ctx.which("node"):
        return []
    code, out = await ctx.exec(["node", "--check", path], timeout=30)
    return [out.strip()[:800]] if code != 0 and out.strip() else []


async def _tsc(ctx, paths):
    if not (ctx.exists("tsconfig.json") and ctx.exists("node_modules/typescript")):
        return []
    code, out = await ctx.exec(["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false", "-p", "."], timeout=int(ctx.options.get("timeout") or 90))
    if code == 0:
        return []
    wanted = {p.replace("\\", "/") for p in paths}
    lines = [l for l in out.splitlines() if "error TS" in l]
    own = [l for l in lines if l.replace("\\", "/").split("(")[0] in wanted]
    others = len(lines) - len(own)
    if others:
        own.append(f"(+ {others} TypeScript error(s) in other files)")
    return own


async def check(ctx, files):
    problems = []
    ts_files = []
    for path in files:
        if not ctx.exists(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in (".py", ".pyi") and ctx.options.get("python", True):
            found = _python(ctx, path)
            problems += found or await _ruff(ctx, path)
        elif ext == ".json" and ctx.options.get("json", True) and not path.endswith(("tsconfig.json", "jsconfig.json")):
            problems += _json(ctx, path)
        elif ext in (".js", ".mjs", ".cjs") and ctx.options.get("javascript", True):
            problems += await _node(ctx, path)
        elif ext in (".ts", ".tsx", ".mts", ".cts") and ctx.options.get("typescript", True):
            ts_files.append(path)
    if ts_files:
        problems += await _tsc(ctx, ts_files)
    return problems


def register(on, mod):
    @on("tool.call", {"tool": EDIT_TOOLS})
    async def diagnose(ctx, e, call_next):
        res = await call_next(e)
        if not res.get("ok"):
            return res
        files = (res.get("metadata") or {}).get("files") or []
        problems = await check(ctx, files) if files else []
        open_problems = ctx.state.setdefault("open", {})
        for f in files:
            open_problems.pop(f, None)
        for line in problems:
            key = next((f for f in files if line.replace("\\", "/").startswith(f)), "other")
            open_problems.setdefault(key, []).append(line)
        total = sum(len(v) for v in open_problems.values())
        ctx.status(f"⚠ {total} problem(s)" if total else "✓ diagnostics")
        ctx.panel("\n".join(f"- `{l[:200]}`" for v in open_problems.values() for l in v) if total else None, "Diagnostics")
        if problems:
            text = "\n".join(problems)
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + "\n..."
            res["output"] = (res.get("output") or "") + f"\n\n[diagnostics] {len(problems)} problem(s) after this edit - fix them:\n{text}"
            res.setdefault("metadata", {})["diagnostics"] = problems[:50]
        return res
