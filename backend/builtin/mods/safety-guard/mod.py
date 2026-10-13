"""Block destructive commands and writes to secrets before they run (PreToolUse-style guard)."""
import fnmatch
import re
import time

DANGEROUS = [
    (r"\brm\s+(-[^\s]*\s+)*-[^\s]*(r[^\s]*f|f[^\s]*r)[^\s]*\s+(--no-preserve-root\s+)?(/|~|\$HOME|/\*|\*|\.\.?/?)(\s|;|&|$)",
     "recursive delete of the root, home, current or parent folder"),
    (r"\brm\s+-[^\s]*\s+--no-preserve-root", "rm --no-preserve-root"),
    (r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", "piping a download straight into a shell"),
    (r"\b(curl|iwr|Invoke-WebRequest)\b[^|;&]*\|\s*(iex|Invoke-Expression)\b", "piping a download into Invoke-Expression"),
    (r"\bchmod\s+(-[^\s]+\s+)*0?777\b", "world-writable permissions (chmod 777)"),
    (r"\bgit\s+push\b[^;&|]*\s(--force(?!-with-lease)|-f)\b", "force push (use --force-with-lease if really needed)"),
    (r"\bgit\s+(reset\s+--hard\s+(origin|HEAD~\d{2,})|clean\s+-[^\s]*[fd][^\s]*[fd])", "irreversible git cleanup"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"(?i)\b(drop\s+(database|schema)|truncate\s+table)\b", "destructive SQL"),
    (r"\bmkfs(\.\w+)?\b|\bdd\s+[^;&|]*\bof=/dev/(sd|nvme|disk|hd)", "writing a raw disk"),
    (r"(?i)\bformat(\.com)?\s+[a-z]:", "formatting a drive"),
    (r"(?i)Remove-Item\b[^;&|]*-Recurse[^;&|]*\s(['\"]?[a-z]:\\?['\"]?(\s|$)|~|\$env:USERPROFILE|\$HOME)", "recursive delete of a drive or home folder"),
    (r"(?i)\b(rd|rmdir)\s+/s\s+(/q\s+)?['\"]?[a-z]:\\?['\"]?(\s|$)", "recursive delete of a drive"),
    (r">\s*/dev/(sd|nvme|disk)", "overwriting a disk device"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b", "publishing a package"),
    (r"(?i)\b(cat|type|Get-Content)\b[^|]*\.(env|pem|key)\b[^|]*\|\s*(curl|wget|nc|ncat|iwr)\b", "sending secrets over the network"),
    (r"\bsudo\s+rm\b", "sudo rm"),
]
EDIT_TOOLS = ["write", "edit", "multiedit", "apply_patch"]
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$|^\*\*\* Move to:\s*(.+)$", re.M)


def _norm(path):
    p = str(path).strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _protected(ctx, path):
    p = _norm(path)
    name = p.rsplit("/", 1)[-1]
    allowed = ctx.options.get("allowedPaths") or []
    if any(fnmatch.fnmatch(p, a) or fnmatch.fnmatch(name, a) for a in allowed):
        return None
    for pat in ctx.options.get("protectedPaths") or []:
        if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(name, pat) or (pat.endswith("/*") and (p + "/").startswith(pat[:-1])) \
                or fnmatch.fnmatch(p, "*/" + pat):
            return pat
    return None


def check_command(ctx, command):
    for pat in ctx.options.get("allowPatterns") or []:
        try:
            if re.search(pat, command):
                return None
        except re.error:
            continue
    rules = DANGEROUS + [(p, "blocked by your extraBlocked rule") for p in (ctx.options.get("extraBlocked") or [])]
    for pat, why in rules:
        try:
            if re.search(pat, command):
                return why
        except re.error:
            continue
    return None


def _block(ctx, what, why):
    log = ctx.state.setdefault("blocked", [])
    log.append({"t": int(time.time() * 1000), "what": what[:200], "why": why})
    del log[:-20]
    ctx.toast(f"Safety guard blocked: {why}", "warning")
    ctx.journal("blocked", what=what[:300], why=why)
    ctx.panel("\n".join(f"- `{b['what'][:120]}` - {b['why']}" for b in reversed(log)), "Safety guard: blocked actions")
    return {"deny": f"{why}. This action is blocked by the safety guard; use a safer, narrower alternative or ask the user."}


def register(on, mod):
    @on("tool.call", {"tool": "bash"})
    async def guard_bash(ctx, e, next):
        command = str((e.get("input") or {}).get("command") or "")
        why = check_command(ctx, command)
        if why:
            return _block(ctx, command, why)
        return await next(e)

    @on("tool.call", {"tool": EDIT_TOOLS})
    async def guard_edits(ctx, e, next):
        args = e.get("input") or {}
        paths = [args.get("path") or args.get("file_path") or ""]
        if e.get("tool") == "apply_patch":
            paths = [a or b for a, b in PATCH_FILE.findall(str(args.get("patch") or ""))]
        for p in paths:
            pat = _protected(ctx, p.strip()) if p else None
            if pat:
                return _block(ctx, f"{e.get('tool')} {p}", f"'{p}' is a protected file ({pat})")
        return await next(e)
