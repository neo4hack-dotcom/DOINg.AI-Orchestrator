"""/commit with an AI-written message, /clean-gone, and a commit band after turns that changed files."""
import os
import re
import tempfile

SYSTEM = ("You write git commit messages. Output ONLY the message: a summary line under 72 characters, a blank line, then "
          "2-6 short bullet points saying what changed and why. No code fences, no quotes, no trailing commentary.")


async def _dirty(ctx):
    code, out = await ctx.run("git status --porcelain", timeout=15)
    return None if code != 0 else [l for l in out.splitlines() if l.strip()]


async def update_band(ctx):
    if not ctx.options.get("showBand", True):
        ctx.band(None)
        return
    files = await _dirty(ctx)
    if files:
        code, branch = await ctx.run("git branch --show-current", timeout=10)
        on = f" on `{branch.strip()}`" if code == 0 and branch.strip() else ""
        ctx.band(f"**{len(files)} uncommitted change(s)**{on}.", [{"label": "Commit with AI message", "command": "/commit"}])
    else:
        ctx.band(None)


def clean_message(text):
    text = re.sub(r"^```\w*\s*|```\s*$", "", text.strip()).strip().strip('"').strip()
    lines = text.splitlines()
    if lines and len(lines[0]) > 100:
        lines[0] = lines[0][:97] + "..."
    return "\n".join(lines).strip()


def register(on, mod):
    mod.command("commit", "Stage all changes and commit them with a generated message", "[hint for the message]")
    mod.command("clean-gone", "Delete local branches whose remote branch was deleted")

    @on("command.run", {"name": "commit"})
    async def commit(ctx, e, call_next):
        if (await ctx.run("git rev-parse --is-inside-work-tree", timeout=10))[0] != 0:
            return {"text": "This folder is not a git repository."}
        code, out = await ctx.run("git add -A", timeout=60)
        if code != 0:
            return {"text": f"`git add -A` failed:\n```\n{out[-1500:]}\n```"}
        _, stat = await ctx.run("git diff --cached --stat", timeout=30)
        if not stat.strip():
            await update_band(ctx)
            return {"text": "Nothing to commit: the working tree is clean."}
        _, diff = await ctx.run("git diff --cached --no-color --unified=2", timeout=60)
        _, log = await ctx.run("git log --oneline -8", timeout=10)
        style = ("Use Conventional Commits: type(scope): summary, with type in feat, fix, refactor, docs, test, chore, "
                 "perf, build, ci, style." if ctx.options.get("conventional", True) else "Match the style of the recent commits.")
        prompt = (f"{style}\nRecent commits (style reference):\n{log.strip() or '(none)'}\n\n"
                  + (f"Hint from the user: {e.get('args')}\n\n" if e.get("args") else "")
                  + f"Files:\n{stat.strip()}\n\nStaged diff (may be cut):\n{diff[:14000]}")
        try:
            message = clean_message(await ctx.llm(prompt, system=SYSTEM))
        except Exception as exc:  # the LLM is down: still commit, with a plain message
            ctx.log(f"LLM unavailable, fallback message: {exc}", "warning")
            message = ""
        if not message:
            message = "chore: update " + ", ".join(l.split("|")[0].strip() for l in stat.splitlines()[:3])
        fd, path = tempfile.mkstemp(prefix="supercode-commit-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(message + "\n")
            code, out = await ctx.run(f'git commit -F "{path}"', timeout=120)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        await update_band(ctx)
        if code != 0:
            return {"text": f"`git commit` failed (pre-commit hook or identity?):\n```\n{out[-2000:]}\n```"}
        _, sha = await ctx.run("git rev-parse --short HEAD", timeout=10)
        ctx.toast(f"Committed {sha.strip()}", "success")
        ctx.journal("commit", sha=sha.strip(), message=message.splitlines()[0])
        return {"text": f"Committed `{sha.strip()}`:\n\n```\n{message}\n```\n\n```\n{stat.strip()}\n```"}

    @on("command.run", {"name": "clean-gone"})
    async def clean_gone(ctx, e, call_next):
        code, out = await ctx.run("git fetch --prune", timeout=120)
        if code != 0:
            return {"text": f"`git fetch --prune` failed:\n```\n{out[-1500:]}\n```"}
        _, branches = await ctx.run("git branch -vv", timeout=20)
        gone = [l[2:].split()[0] for l in branches.splitlines() if ": gone]" in l and not l.startswith("*")]
        deleted, failed = [], []
        for b in gone:
            code, out = await ctx.run(f'git branch -D "{b}"', timeout=20)
            (deleted if code == 0 else failed).append(b)
        if not gone:
            return {"text": "No local branch has a deleted remote."}
        return {"text": f"Deleted {len(deleted)} branch(es): {', '.join(deleted) or '-'}" + (f"\nFailed: {', '.join(failed)}" if failed else "")}

    @on("turn.complete")
    async def offer(ctx, e):
        await update_band(ctx)

    @on("session.start")
    async def start(ctx, e):
        await update_band(ctx)
