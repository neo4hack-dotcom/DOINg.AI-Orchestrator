"""Status line: branch + uncommitted files, model, context usage, tokens, last turn time."""
import time

EDITS = ("write", "edit", "multiedit", "apply_patch", "bash")


def _k(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _duration(ms):
    s = int(ms / 1000)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


async def _git(ctx):
    code, out = await ctx.run("git status --porcelain=v1 --branch", timeout=10)
    if code != 0:
        return None
    lines = [l for l in out.splitlines() if l.strip()]
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    branch = head.replace("No commits yet on ", "").split("...")[0].split(" ")[0] or "?"
    return {"branch": branch, "dirty": sum(1 for l in lines[1:] if not l.startswith("##"))}


async def refresh(ctx, git):
    s = ctx.session
    items = []
    if ctx.options.get("showGit", True):
        if git or "git" not in ctx.state:
            ctx.state["git"] = await _git(ctx)
        g = ctx.state.get("git")
        if g:
            items.append(f"⎇ {g['branch']}" + (f" ●{g['dirty']}" if g["dirty"] else " ✓"))
    if s.get("model"):
        items.append(str(s["model"]))
    window, used = s.get("contextWindow") or 0, s.get("contextTokens") or 0
    if window and used:
        items.append(f"ctx {min(999, round(100 * used / window))}%")
    tok = s.get("tokens") or {}
    if ctx.options.get("showTokens", True) and (tok.get("input") or tok.get("output")):
        items.append(f"↑{_k(tok.get('input', 0))} ↓{_k(tok.get('output', 0))}")
    if ctx.options.get("showTurnTime", True) and ctx.state.get("lastTurnMs"):
        items.append(f"⏱ {_duration(ctx.state['lastTurnMs'])}")
    ctx.status("  ·  ".join(items) or None)


def register(on, mod):
    @on("session.start")
    async def start(ctx, e):
        await refresh(ctx, True)

    @on("prompt.submit")
    def submitted(ctx, e):
        ctx.state["turnStarted"] = time.time()

    @on("tool.call")
    async def after_tool(ctx, e, next):
        res = await next(e)
        await refresh(ctx, e.get("tool") in EDITS)
        return res

    @on("turn.complete")
    async def done(ctx, e):
        started = ctx.state.pop("turnStarted", None)
        if started:
            ctx.state["lastTurnMs"] = int((time.time() - started) * 1000)
        await refresh(ctx, True)
