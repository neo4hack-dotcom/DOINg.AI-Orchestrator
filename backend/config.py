"""Persistent settings: local LLM, savvy tiers, permissions, flow options.

Stored in ``data/config.json``. The LLM block mirrors NEXUS.AI's ``llmConfig``
(provider / baseUrl / apiKey / model / systemPrompt) so it can be imported as is.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from .osutil import atomic_write

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORCH_DATA_DIR", ROOT_DIR / "data"))
CONFIG_FILE = DATA_DIR / "config.json"

PROVIDERS = ("ollama", "openai_compatible", "n8n")
TIERS = ("fable", "heavy", "careful", "medium", "light")
EFFORTS = ("low", "medium", "high", "xhigh")
PERMISSION_VALUES = ("allow", "ask", "deny")

# Effort sets how many tool steps a worker may take and how hard it checks itself.
EFFORT_MAX_STEPS = {"low": 12, "medium": 20, "high": 30, "xhigh": 40}

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "ollama",
        "baseUrl": "http://localhost:11434",
        "apiKey": "",
        "model": "llama3",
        "systemPrompt": "",
        # auto: native tool calling, falling back to the <tool_call> text protocol
        # when the server or model refuses tools. n8n always uses the text protocol.
        "toolMode": "auto",
        "contextWindow": 16384,
        "temperature": 0.2,
        "timeout": 600,
        # Language of the agents' answers (the app UI is English).
        "responseLanguage": "English",
    },
    # Same ladder as savvy-flow; an empty model means "use llm.model".
    "tiers": {
        "fable": {"model": "", "effort": "high"},
        "heavy": {"model": "", "effort": "xhigh"},
        "careful": {"model": "", "effort": "high"},
        "medium": {"model": "", "effort": "medium"},
        "light": {"model": "", "effort": "low"},
    },
    "orchestratorModel": "",
    "workspace": str(Path.cwd()),
    # Where "New project" creates folders by default, and the folders opened recently.
    "projectsRoot": str(Path.home() / "SuperCodeProjects"),
    "recentProjects": [],
    # Shell for the bash tool: auto (bash on macOS/Linux; Git Bash on Windows, else PowerShell),
    # bash, powershell, cmd, or the path of a shell executable.
    "shell": "auto",
    # Auto mode: no human in the loop. Plans are approved, permissions granted (deny rules still
    # apply), questions answered by the agents themselves with stated assumptions.
    "autoMode": False,
    # Pull requests to Bitbucket (Server / Data Center or Cloud).
    "bitbucket": {
        "kind": "server",               # server (Data Center, self-hosted) | cloud (bitbucket.org)
        "baseUrl": "",                  # https://bitbucket.company.com (server); ignored for cloud
        "project": "",                  # server: project key; cloud: workspace. Empty = from the git remote
        "repo": "",                     # repository slug. Empty = from the git remote
        "username": "",                 # server: user slug (basic auth); cloud: Bitbucket username / email
        "token": "",                    # server: HTTP access token; cloud: app password / API token
        "targetBranch": "",             # empty = the repository default branch
        "branchPrefix": "supercode/",
        "reviewers": [],                # server: user slugs; cloud: account ids / uuids
        "remote": "origin",
        "autoPrOnFlow": False,          # open a PR automatically when a savvy-flow finishes
        "verifySsl": True,
    },
    "permissions": {
        "edit": "allow",
        "bash": "ask",
        "webfetch": "ask",
        # Evaluated per sub-command (split on && ; | ||). deny wins, then allow; else the category value.
        "bashRules": [
            {"pattern": "ls*", "action": "allow"},
            {"pattern": "pwd", "action": "allow"},
            {"pattern": "cat *", "action": "allow"},
            {"pattern": "head *", "action": "allow"},
            {"pattern": "tail *", "action": "allow"},
            {"pattern": "wc *", "action": "allow"},
            {"pattern": "find *", "action": "allow"},
            {"pattern": "grep *", "action": "allow"},
            {"pattern": "rg *", "action": "allow"},
            {"pattern": "echo *", "action": "allow"},
            {"pattern": "git status*", "action": "allow"},
            {"pattern": "git diff*", "action": "allow"},
            {"pattern": "git log*", "action": "allow"},
            {"pattern": "git show*", "action": "allow"},
            {"pattern": "dir*", "action": "allow"},
            {"pattern": "type *", "action": "allow"},
            {"pattern": "where *", "action": "allow"},
            {"pattern": "Get-ChildItem*", "action": "allow"},
            {"pattern": "Get-Content*", "action": "allow"},
            {"pattern": "rd /s*", "action": "deny"},
            {"pattern": "rmdir /s*", "action": "deny"},
            {"pattern": "del /s*", "action": "deny"},
            {"pattern": "format *", "action": "deny"},
            {"pattern": "Remove-Item*-Recurse*", "action": "deny"},
            {"pattern": "Stop-Computer*", "action": "deny"},
            {"pattern": "sudo *", "action": "deny"},
            {"pattern": "rm -rf /*", "action": "deny"},
            {"pattern": "rm -rf ~*", "action": "deny"},
            {"pattern": "git push*", "action": "deny"},
            {"pattern": "git reset --hard*", "action": "deny"},
            {"pattern": "shutdown*", "action": "deny"},
            {"pattern": "reboot*", "action": "deny"},
            {"pattern": "mkfs*", "action": "deny"},
            {"pattern": "dd if=*", "action": "deny"},
        ],
    },
    "flow": {
        "requirePlanApproval": True,
        "maxParallel": 1,
        "maxReturnRounds": 2,
        "gitCommit": False,
        "planSteps": 8,
        "reviewSteps": 8,
        # Plan decomposition
        "hierarchical": True,           # modules first, then tasks per module, for large requests
        "splitLargeTasks": True,        # ask the orchestrator to split L / very complex tasks
        "mergeSmallTasks": True,        # merge chains of tiny tasks to save worker spin-ups
        # Execution
        "autoVerify": True,             # run verify commands after each worker, feed failures back
        "autoFixRetries": 2,            # worker self-fix attempts before the review
        "fastAcceptLight": True,        # light task + passing verify command => accepted without LLM review
        "escalateOnFail": True,         # one last attempt one tier up before failing a task
        "integrationRounds": 2,         # final verify + fix-task rounds
        "taskTimeoutMinutes": 45,
        "tokenBudget": 0,               # 0 = unlimited; stops delegating when exceeded
        "autoResume": True,             # resume interrupted flows when the server restarts
        "writeReport": True,            # docs/FLOW_REPORT.md at close
    },
    "verify": {"commands": [], "timeout": 300},
    "context": {
        "toolOutputMaxChars": 12000,    # longer outputs spill to a file the model can read
        "pruneAt": 0.5,                 # clear old tool outputs above this share of the window
        "compactAt": 0.75,              # summarize old history above this share of the window
        "keepRecentShare": 0.3,         # share of the window kept verbatim
        "repoMap": True,
        "repoMapChars": 6000,
    },
    "agent": {
        "maxRetries": 4,                # transient LLM errors (connection, 5xx, 429, timeout)
        "retryBaseSeconds": 2.0,        # backoff: base * 2^attempt (+ jitter), capped at 60 s
        "doomLoopThreshold": 3,         # identical tool calls in a row before intervening
        "requireReadBeforeEdit": True,
        "goalMaxContinuations": 8,
    },
    "nexusDbPath": "",
}

_lock = threading.Lock()


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _sanitize(cfg: dict) -> dict:
    # Migration: permissions.autonomous (v2) -> autoMode.
    if cfg["permissions"].pop("autonomous", False):
        cfg["autoMode"] = True
    cfg.pop("language", None)
    bb = cfg["bitbucket"]
    if bb.get("kind") not in ("server", "cloud"):
        bb["kind"] = "server"
    if not isinstance(bb.get("reviewers"), list):
        bb["reviewers"] = [r.strip() for r in str(bb.get("reviewers") or "").split(",") if r.strip()]
    cfg["recentProjects"] = [str(p) for p in (cfg.get("recentProjects") or []) if str(p).strip()][:15]
    llm = cfg["llm"]
    if llm.get("provider") not in PROVIDERS:
        llm["provider"] = "ollama"
    if llm.get("toolMode") not in ("auto", "native", "prompt"):
        llm["toolMode"] = "auto"
    for name in TIERS:
        tier = cfg["tiers"].setdefault(name, copy.deepcopy(DEFAULT_CONFIG["tiers"][name]))
        if tier.get("effort") not in EFFORTS:
            tier["effort"] = DEFAULT_CONFIG["tiers"][name]["effort"]
        tier["model"] = str(tier.get("model") or "")
    for key in ("edit", "bash", "webfetch"):
        if cfg["permissions"].get(key) not in PERMISSION_VALUES:
            cfg["permissions"][key] = DEFAULT_CONFIG["permissions"][key]
    rules = cfg["permissions"].get("bashRules")
    if not isinstance(rules, list):
        rules = []
    cfg["permissions"]["bashRules"] = [
        {"pattern": str(r.get("pattern", "")).strip(), "action": r.get("action")}
        for r in rules
        if isinstance(r, dict) and str(r.get("pattern", "")).strip() and r.get("action") in PERMISSION_VALUES
    ]
    if not isinstance(cfg["verify"].get("commands"), list):
        cfg["verify"]["commands"] = []
    cfg["verify"]["commands"] = [str(c).strip() for c in cfg["verify"]["commands"] if str(c).strip()]
    flow = cfg["flow"]
    flow["maxParallel"] = max(1, min(8, int(flow.get("maxParallel") or 1)))
    flow["maxReturnRounds"] = max(0, min(5, int(flow.get("maxReturnRounds") or 0)))
    return cfg


def load_config() -> dict:
    with _lock:
        stored: dict = {}
        if CONFIG_FILE.exists():
            try:
                stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                stored = {}
        return _sanitize(_deep_merge(DEFAULT_CONFIG, stored))


def save_config(update: dict) -> dict:
    cfg = _sanitize(_deep_merge(load_config(), update or {}))
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False))
    return cfg


def tier_model(cfg: dict, tier: str) -> str:
    return (cfg["tiers"].get(tier) or {}).get("model") or cfg["llm"]["model"]


def tier_effort(cfg: dict, tier: str) -> str:
    return (cfg["tiers"].get(tier) or {}).get("effort") or "medium"


def orchestrator_model(cfg: dict) -> str:
    return cfg.get("orchestratorModel") or cfg["llm"]["model"]


SECRET = "********"
SECRET_PATHS = (("llm", "apiKey"), ("bitbucket", "token"))


def public_config(cfg: dict) -> dict:
    """The config sent to the browser: secrets masked."""
    out = copy.deepcopy(cfg)
    for section, key in SECRET_PATHS:
        if out.get(section, {}).get(key):
            out[section][key] = SECRET
    return out


def strip_masked(update: dict) -> dict:
    """A save from the browser keeps stored secrets when it sends the mask back."""
    for section, key in SECRET_PATHS:
        if isinstance(update.get(section), dict) and update[section].get(key) == SECRET:
            update[section].pop(key)
    return update


def remember_project(directory: str) -> None:
    cfg = load_config()
    recent = [directory] + [p for p in cfg.get("recentProjects", []) if p != directory]
    save_config({"recentProjects": recent[:15]})


def auto_mode(cfg: dict, session_data: dict | None = None) -> bool:
    if session_data is not None and session_data.get("autoMode") is not None:
        return bool(session_data["autoMode"])
    return bool(cfg.get("autoMode"))


# --------------------------------------------------------------------------
# NEXUS.AI import

NEXUS_CANDIDATES = (
    "../NEXUS.AI/db.json",
    "../nexus.ai/db.json",
    "../NEXUS.AI/server-config.json",
    "../nexus.ai/server-config.json",
)


def _resolve_nexus_db(path_hint: str) -> Path | None:
    candidates = [path_hint] if path_hint else []
    candidates += [str((ROOT_DIR / c).resolve()) for c in NEXUS_CANDIDATES]
    for raw in candidates:
        p = Path(raw).expanduser()
        if p.is_dir():
            # A NEXUS.AI checkout: honour its server-config.json redirect.
            redirect = p / "server-config.json"
            if redirect.exists():
                try:
                    db = json.loads(redirect.read_text(encoding="utf-8")).get("dbPath")
                    if db and Path(db).exists():
                        return Path(db)
                except (OSError, json.JSONDecodeError):
                    pass
            p = p / "db.json"
        elif p.name == "server-config.json" and p.exists():
            try:
                db = json.loads(p.read_text(encoding="utf-8")).get("dbPath")
                p = Path(db) if db else p.with_name("db.json")
            except (OSError, json.JSONDecodeError):
                p = p.with_name("db.json")
        if p.exists() and p.is_file():
            return p
    return None


def read_nexus_llm_config(path_hint: str) -> tuple[dict, Path]:
    """Return NEXUS.AI's llmConfig mapped onto our ``llm`` block."""
    db = _resolve_nexus_db(path_hint)
    if not db:
        raise FileNotFoundError(
            "NEXUS.AI db.json not found. Give the path to the NEXUS.AI folder or to its db.json."
        )
    data = json.loads(db.read_text(encoding="utf-8"))
    src = data.get("llmConfig") or {}
    if not src:
        raise ValueError(f"{db} has no llmConfig block")
    llm = {
        "provider": src.get("provider") if src.get("provider") in PROVIDERS else "ollama",
        "baseUrl": src.get("baseUrl") or DEFAULT_CONFIG["llm"]["baseUrl"],
        "apiKey": src.get("apiKey") or "",
        "model": src.get("model") or DEFAULT_CONFIG["llm"]["model"],
    }
    return llm, db
