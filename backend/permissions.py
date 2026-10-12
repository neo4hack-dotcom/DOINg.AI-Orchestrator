"""Permission decisions: category defaults, bash pattern rules, session grants.

Rules use shell-style wildcards (``git status*``). A compound command is split into
sub-commands; any deny wins, the command is allowed only when every sub-command is
allowed, otherwise the category default applies (OpenCode-style).
"""
from __future__ import annotations

import fnmatch
import re
import shlex

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def split_commands(command: str) -> list[str]:
    return [c.strip() for c in _SPLIT.split(command or "") if c.strip()]


def _match(pattern: str, command: str) -> bool:
    return fnmatch.fnmatchcase(command, pattern) or fnmatch.fnmatchcase(command, pattern.rstrip("*").rstrip())


def bash_decision(command: str, rules: list[dict], default: str, granted: list[str]) -> str:
    subs = split_commands(command) or [command]
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
