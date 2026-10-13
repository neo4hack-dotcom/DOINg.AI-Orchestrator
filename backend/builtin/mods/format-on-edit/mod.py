"""Run the project's formatter on every file the agent edits (PostToolUse-style hook)."""
import json
import os

EDIT_TOOLS = ["write", "edit", "multiedit", "apply_patch"]
PRETTIER_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".scss", ".less", ".json", ".md", ".html", ".vue",
                ".svelte", ".yaml", ".yml", ".graphql"}
PRETTIER_CONFIGS = (".prettierrc", ".prettierrc.json", ".prettierrc.js", ".prettierrc.cjs", ".prettierrc.mjs", ".prettierrc.yaml",
                    ".prettierrc.yml", ".prettierrc.toml", "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs")


def _pyproject(ctx):
    try:
        return ctx.read("pyproject.toml") if ctx.exists("pyproject.toml") else ""
    except OSError:
        return ""


def _prettier_configured(ctx):
    if any(ctx.exists(c) for c in PRETTIER_CONFIGS):
        return True
    try:
        return "prettier" in json.loads(ctx.read("package.json")) if ctx.exists("package.json") else False
    except (OSError, ValueError):
        return False


def pick(ctx, path):
    """(formatter name, command) for this file, or None."""
    ext = os.path.splitext(path)[1].lower()
    only = ctx.options.get("onlyConfigured", True)
    # Argument lists (no shell): a file name can never be read as shell syntax.
    if ext in PRETTIER_EXT and ctx.exists("node_modules/prettier") and (not only or _prettier_configured(ctx)):
        return "prettier", ["npx", "--no-install", "prettier", "--write", "--log-level", "warn", "--", path]
    if ext in (".py", ".pyi"):
        py = _pyproject(ctx)
        if ctx.which("ruff") and (not only or "[tool.ruff" in py or ctx.exists("ruff.toml") or ctx.exists(".ruff.toml")):
            return "ruff", ["ruff", "format", "--quiet", "--", path]
        if ctx.which("black") and (not only or "[tool.black" in py):
            return "black", ["black", "--quiet", "--", path]
    if ext == ".go" and ctx.which("gofmt"):
        return "gofmt", ["gofmt", "-w", path]
    if ext == ".rs" and ctx.which("rustfmt") and (not only or ctx.exists("rustfmt.toml") or ctx.exists(".rustfmt.toml") or ctx.exists("Cargo.toml")):
        return "rustfmt", ["rustfmt", "--edition", "2021", path]
    return None


def register(on, mod):
    @on("tool.call", {"tool": EDIT_TOOLS})
    async def format_files(ctx, e, next):
        res = await next(e)
        if not res.get("ok"):
            return res
        done = []
        for path in (res.get("metadata") or {}).get("files") or []:
            choice = pick(ctx, path)
            if not choice or not ctx.exists(path):
                continue
            name, command = choice
            before = ctx.read(path)
            code, out = await ctx.exec(command, timeout=int(ctx.options.get("timeout") or 60))
            if code != 0:
                ctx.log(f"{name} failed on {path}: {out[-400:]}", "warning")
                continue
            if ctx.read(path) != before:
                done.append(f"{path} ({name})")
        if done:
            ctx.state["formatted"] = ctx.state.get("formatted", 0) + len(done)
            res["output"] = (res.get("output") or "") + ("\n\n[format-on-edit] Reformatted " + ", ".join(done)
                                                         + ". The content changed: read the file again before editing it.")
        return res
