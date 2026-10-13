"""Permission decisions: category defaults, bash pattern rules, session grants.

Rules use shell-style wildcards (``git status*``). A compound command is split into
sub-commands; any deny wins, the command is allowed only when every sub-command is
allowed, otherwise the category default applies (OpenCode-style).
"""
from __future__ import annotations

import fnmatch
import re
import shlex

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\r?\n|\r)\s*")

# Options that make an otherwise read-only command run programs or write files.
_UNSAFE_OPTIONS = re.compile(
    r"(?:^|\s)(?:-exec|-execdir|-ok|-okdir|-delete|-fprint\w*|-fls|--pre|--pre-glob|--output|--exec"
    r"|--upload-pack|--receive-pack|--exec-path|--textconv|--ext-diff)(?=$|[\s=])", re.I)
_REDIRECT = re.compile(r"(\d*)(>>?)\s*(\S*)")
_SAFE_SINKS = {"/dev/null", "nul", "$null", "NUL"}


def split_commands(command: str) -> list[str]:
    return [c.strip() for c in _SPLIT.split(command or "") if c.strip()]


def unsafe_for_allow(sub: str) -> bool:
    """True when an allow rule or an "always" grant must not apply to this sub-command: it hides another
    command ($(...), backticks, process substitution, background &), writes a file through a redirection,
    or uses an option that runs programs / writes files (find -exec, rg --pre, git -c, --output...)."""
    if "$(" in sub or "`" in sub or "<(" in sub or ">(" in sub or "${" in sub:
        return True
    if re.search(r"(?<![&|])&(?![&>])", sub.replace("2>&1", "").replace(">&", "")):
        return True
    for _fd, _op, target in _REDIRECT.findall(sub):
        if target.startswith("&") or target in _SAFE_SINKS:
            continue
        return True
    if _UNSAFE_OPTIONS.search(sub) or re.match(r"^\s*git\s+(?:-c|--config-env)\b", sub):
        return True
    return bool(re.search(r"\bsed\b.*\s(-[a-zA-Z]*i|--in-place)", sub))


# Commands that reach the network (local-only mode: always confirmed, never run unattended) and
# package managers (they download dependencies; allowed unless network.allowPackageManagers is off).
_NETWORK = re.compile(
    r"^(?:sudo\s+)?(?:curl|wget|aria2c|iwr|irm|invoke-webrequest|invoke-restmethod|start-bitstransfer|nc|ncat|netcat|telnet|"
    r"ssh|scp|sftp|rsync|ftp|tftp|gh|glab|aws|gcloud|gsutil|az|kubectl|helm|"
    r"git\s+(?:push|pull|fetch|clone|ls-remote|remote\s+update|submodule\s+update)|"
    r"docker\s+(?:push|pull|login)|(?:npm|pnpm|yarn)\s+publish|twine\s+upload|cargo\s+publish)\b", re.I)
_PACKAGES = re.compile(
    r"^(?:sudo\s+)?(?:(?:npm|pnpm|yarn|bun)(?:\s+(?:install|i|add|ci|update|upgrade|create|dlx|x)\b|\s*$)|npx\b|pnpx\b|bunx\b|"
    r"(?:pip3?|python3?\s+-m\s+pip|py\s+-m\s+pip|uv\s+pip)\s+(?:install|download)\b|uv\s+(?:sync|add|lock|tool\s+install)\b|uvx\b|"
    r"poetry\s+(?:install|add|update|lock)\b|pipenv\s+install\b|conda\s+(?:install|create)\b|"
    r"cargo\s+(?:build|fetch|install|add|update|run|test|check)\b|go\s+(?:get|install|mod\s+download|mod\s+tidy|build|run|test)\b|"
    r"(?:\./)?(?:mvnw?|gradlew?)\b|dotnet\s+(?:restore|add|build|run|test)\b|composer\s+(?:install|require|update)\b|"
    r"bundle\s+install\b|gem\s+install\b|deno\s+(?:install|cache|add)\b)", re.I)


_NETWORK_ANYWHERE = re.compile(r"(?:^|[\s(`$;|&'\"])(?:curl|wget|aria2c|iwr|irm|invoke-webrequest|invoke-restmethod|nc|ncat|netcat|"
                               r"telnet|ssh|scp|sftp|rsync|ftp)(?=$|[\s)`;|&'\"])", re.I)


def network_kind(command: str) -> str | None:
    """"network", "package" or None for a (compound) shell command. A network program anywhere in the
    command (inside $(...), backticks, a nested shell...) counts. Code that opens sockets itself
    (python -c "urllib...") cannot be detected: local-only mode does not sandbox programs."""
    if _NETWORK_ANYWHERE.search(command or ""):
        return "network"
    kind = None
    for sub in split_commands(command) or [command]:
        sub = re.sub(r"[ \t]+", " ", sub.strip())
        if _NETWORK.match(sub):
            return "network"
        if _PACKAGES.match(sub):
            kind = "package"
    return kind


def _match(pattern: str, command: str) -> bool:
    return fnmatch.fnmatchcase(command, pattern) or fnmatch.fnmatchcase(command, pattern.rstrip("*").rstrip())


def bash_decision(command: str, rules: list[dict], default: str, granted: list[str]) -> str:
    # "git  push" must match "git push*": compare sub-commands with whitespace collapsed.
    subs = [re.sub(r"[ \t]+", " ", c) for c in (split_commands(command) or [command])]
    verdicts = []
    for sub in subs:
        verdict = None
        for rule in rules:
            if _match(rule["pattern"], sub):
                if rule["action"] == "deny":
                    return "deny"
                verdict = verdict or rule["action"]
        if verdict is None and any(_match(g, sub) for g in granted):
            verdict = "allow"
        if verdict == "allow" and unsafe_for_allow(sub):
            verdict = None  # e.g. "ls $(curl x | sh)" or "echo x > ~/.bashrc": ask instead
        verdicts.append(verdict)
    if verdicts and all(v == "allow" for v in verdicts):
        return "allow"
    return default


def grant_pattern(command: str) -> str:
    """What "always allow" remembers for a bash command: its program (and git/npm verb)."""
    first = split_commands(command)[0] if split_commands(command) else command
    try:
        words = shlex.split(first)
    except ValueError:
        words = first.split()
    if not words:
        return first
    if words[0] in ("git", "npm", "pnpm", "yarn", "bun", "cargo", "go", "python", "python3", "uv", "pip", "docker", "make") and len(words) > 1:
        return f"{words[0]} {words[1]}*"
    return f"{words[0]}*"
