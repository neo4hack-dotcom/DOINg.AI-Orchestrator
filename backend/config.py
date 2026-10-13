"""Persistent settings: local LLM, savvy tiers, permissions, flow options.

Stored in ``data/config.json``. The LLM block mirrors NEXUS.AI's ``llmConfig``
(provider / baseUrl / apiKey / model / systemPrompt) so it can be imported as is.
"""
from __future__ import annotations

import copy
import json
import os
import re
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
# Claude Code-style permission modes: ask before edits / accept edits / auto (no prompts; deny rules still apply).
PERMISSION_MODES = ("ask", "acceptEdits", "auto")

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
    # Default folder for sessions: the projects root (never the app's own folder).
    "workspace": str(Path.home() / "SuperCodeProjects"),
    # Where "New project" creates folders by default, and the folders opened recently.
    "projectsRoot": str(Path.home() / "SuperCodeProjects"),
    "recentProjects": [],
    # Shell for the bash tool: auto (bash on macOS/Linux; Git Bash on Windows, else PowerShell),
    # bash, powershell, cmd, or the path of a shell executable.
    "shell": "auto",
    # Permission mode for new sessions (Shift+Tab switches it per session):
    #   ask         - confirm file edits, shell commands, opening apps, MCP / data writes;
    #   acceptEdits - file edits applied without asking, the rest asks;
    #   auto        - no human in the loop: plans approved, permissions granted (deny rules and the
    #                 safety guard still apply), questions answered by the agents with stated assumptions.
    "permissionMode": "ask",
    "autoMode": False,                  # mirror of permissionMode == "auto" (older clients)
    # Privacy: the app's own connections (LLM, webfetch, MCP HTTP servers, Bitbucket) stay on this
    # machine or the private network. Package installs by the agents can be allowed separately.
    "network": {"localOnly": True, "allowPackageManagers": True},
    # Data analysis add-in: databases through MCP servers, local SQLite / CSV files, tables, exports.
    "data": {"enabled": True, "maxRows": 5000, "previewRows": 15},
    # Integrated terminal and background processes.
    "terminal": {"maxProcesses": 8, "shellTimeout": 120},
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
        # ask = follow the permission mode (Ask confirms edits); allow = never confirm edits; deny = read-only.
        "edit": "ask",
        "bash": "ask",
        "webfetch": "ask",
        # MCP tool calls (per-server overrides in mcp.servers.<name>.permission; read-only tools can be trusted).
        "mcp": "ask",
        # Installing / enabling a mod written by the agent (mods run inside the server).
        "mods": "ask",
        # Opening a file, folder or URL with the system's default application.
        "open": "ask",
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
    # Model Context Protocol servers (Claude Desktop / Claude Code format, plus our options).
    "mcp": {
        # name -> {type: stdio|http|sse, command, args, env, cwd, url, headers, enabled, permission (ask|allow|deny),
        #          trustReadOnly, timeout, disabledTools, description}
        "servers": {},
        # "<resolved project dir>::<server>" entries the user approved from a project's .mcp.json
        "approvedProjectServers": [],
        # Above this many MCP tools, the agents get mcp_search + mcp_call instead of every schema (saves context).
        "deferThreshold": 16,
        "maxDescriptionChars": 400,
        "inPlanMode": True,              # plan agent / orchestrator get the read-only MCP tools
        "inWorkers": True,               # savvy workers get the MCP tools
    },
    # Agent Skills (SKILL.md folders): builtin, user (data/skills) and project (.supercode/skills, .claude/skills).
    "skills": {"disabled": [], "modelInvocation": True},
    # Mods: Python hooks on the agent's events (builtin + data/mods).
    "mods": {"disabled": [], "enabled": [], "options": {}},
    # Optional reverse-engineering layer (REA, https://github.com/morluto/rea, MIT) - lawful use only.
    "reverse": {
        "enabled": False,
        "acknowledged": False,
        "version": "6.4.0",
        "provider": "auto",              # auto | hopper | ghidra | ida  (REA_ANALYSIS_PROVIDER)
        "schemaProfile": "compact",      # REA_MCP_INPUT_SCHEMA_PROFILE: compact tool schemas suit local models
        "timeout": 900,
        "env": {},
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
    for key in ("edit", "bash", "webfetch", "mcp", "mods", "open"):
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
    mcp = cfg["mcp"]
    if not isinstance(mcp.get("servers"), dict):
        mcp["servers"] = {}
    mcp["servers"] = {str(n): _sanitize_server(v) for n, v in mcp["servers"].items() if isinstance(v, dict) and str(n).strip()}
    mcp["approvedProjectServers"] = [str(x) for x in (mcp.get("approvedProjectServers") or [])]
    mcp["deferThreshold"] = max(0, int(mcp.get("deferThreshold") or 0))
    for section in ("skills", "mods"):
        cfg[section]["disabled"] = sorted({str(x) for x in (cfg[section].get("disabled") or [])})
    cfg["mods"]["enabled"] = sorted({str(x) for x in (cfg["mods"].get("enabled") or [])})
    if not isinstance(cfg["mods"].get("options"), dict):
        cfg["mods"]["options"] = {}
    mode = cfg.get("permissionMode")
    if mode not in PERMISSION_MODES:
        mode = "auto" if cfg.get("autoMode") else "ask"
    cfg["permissionMode"] = mode
    cfg["autoMode"] = mode == "auto"
    cfg["network"]["localOnly"] = bool(cfg["network"].get("localOnly", True))
    cfg["network"]["allowPackageManagers"] = bool(cfg["network"].get("allowPackageManagers", True))
    cfg["data"]["maxRows"] = max(10, min(200_000, int(cfg["data"].get("maxRows") or 5000)))
    cfg["data"]["previewRows"] = max(3, min(100, int(cfg["data"].get("previewRows") or 15)))
    cfg["terminal"]["maxProcesses"] = max(1, min(32, int(cfg["terminal"].get("maxProcesses") or 8)))
    rev = cfg["reverse"]
    # The version ends up in a command line: a package version or dist-tag only.
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,40}", str(rev.get("version") or "")):
        rev["version"] = "6.4.0"
    if rev.get("provider") not in ("auto", "hopper", "ghidra", "ida"):
        rev["provider"] = "auto"
    if rev.get("schemaProfile") not in ("compact", "full"):
        rev["schemaProfile"] = "compact"
    rev["enabled"] = bool(rev.get("enabled")) and bool(rev.get("acknowledged"))
    flow = cfg["flow"]
    flow["maxParallel"] = max(1, min(8, int(flow.get("maxParallel") or 1)))
    flow["maxReturnRounds"] = max(0, min(5, int(flow.get("maxReturnRounds") or 0)))
    return cfg


def _sanitize_server(raw: dict) -> dict:
    srv = {k: v for k, v in raw.items() if k != "source"}
    kind = srv.get("type") or ("http" if srv.get("url") else "stdio")
    srv["type"] = kind if kind in ("stdio", "http", "sse") else "stdio"
    srv["enabled"] = bool(srv.get("enabled", True))
    if srv.get("permission") not in PERMISSION_VALUES:
        srv["permission"] = "ask"
    srv["trustReadOnly"] = bool(srv.get("trustReadOnly", True))
    srv["args"] = [str(a) for a in (srv.get("args") or [])] if isinstance(srv.get("args"), list) else []
    for key in ("env", "headers"):
        srv[key] = {str(k): str(v) for k, v in (srv.get(key) or {}).items()} if isinstance(srv.get(key), dict) else {}
    srv["disabledTools"] = [str(t) for t in (srv.get("disabledTools") or [])]
    return srv


def load_config() -> dict:
    with _lock:
        stored: dict = {}
        if CONFIG_FILE.exists():
            try:
                stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                stored = {}
        if stored and "permissionMode" not in stored:
            # v5 migration: "edit: allow" was the old default; the permission mode now decides.
            perms = stored.get("permissions") or {}
            if perms.get("edit") == "allow":
                perms["edit"] = "ask"
        return _sanitize(_deep_merge(DEFAULT_CONFIG, stored))


def save_config(update: dict) -> dict:
    update = dict(update or {})
    if "autoMode" in update and "permissionMode" not in update:  # older clients toggle autoMode only
        update["permissionMode"] = "auto" if update["autoMode"] else "ask"
    cfg = _sanitize(_deep_merge(load_config(), update))
    _write(cfg)
    return cfg


def update_config(mutate) -> dict:
    """Read-modify-write for changes a deep merge cannot express (deleting a server, a mod option...)."""
    cfg = load_config()
    mutate(cfg)
    cfg = _sanitize(cfg)
    _write(cfg)
    return cfg


def _write(cfg: dict) -> None:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False))


def tier_model(cfg: dict, tier: str) -> str:
    return (cfg["tiers"].get(tier) or {}).get("model") or cfg["llm"]["model"]


def tier_effort(cfg: dict, tier: str) -> str:
    return (cfg["tiers"].get(tier) or {}).get("effort") or "medium"


def orchestrator_model(cfg: dict) -> str:
    return cfg.get("orchestratorModel") or cfg["llm"]["model"]


SECRET = "********"
SECRET_PATHS = (("llm", "apiKey"), ("bitbucket", "token"))


SECRET_KEY = re.compile(r"(key|token|secret|passw|auth|credential|cookie|session)", re.I)


URL_CREDENTIALS = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")


def _mask_text(value: str, previous: str = "") -> str:
    value = URL_CREDENTIALS.sub(lambda m: m.group(1) + SECRET + m.group(3), value)
    m = re.match(r"^(--?[\w-]+=)(.+)$", value)
    if m and SECRET_KEY.search(m.group(1)):
        return m.group(1) + SECRET
    if previous and re.match(r"^--?[\w-]+$", previous) and SECRET_KEY.search(previous):
        return SECRET
    return value


def mask_server(srv: dict) -> dict:
    """MCP server for the browser: env / header values that look like secrets, credentials in URLs and
    secret-looking command-line arguments (--api-key x, --token=x, postgres://user:pw@host) are masked."""
    out = copy.deepcopy(srv)
    for section in ("env", "headers"):
        for k, v in (out.get(section) or {}).items():
            if v and SECRET_KEY.search(k):
                out[section][k] = SECRET
    if out.get("url"):
        out["url"] = _mask_text(str(out["url"]))
    args = [str(a) for a in out.get("args") or []]
    out["args"] = [_mask_text(a, args[i - 1] if i else "") for i, a in enumerate(args)]
    return out


def unmask_server(new: dict, old: dict | None) -> dict:
    """Masked values sent back by the browser keep the stored secret."""
    if old:
        masked_old = mask_server(old)
        if new.get("url") and SECRET in str(new["url"]) and new["url"] == masked_old.get("url"):
            new["url"] = old.get("url")
        old_args, shown = [str(a) for a in old.get("args") or []], masked_old.get("args") or []
        new["args"] = [old_args[i] if SECRET in str(a) and i < len(shown) and a == shown[i] else a
                       for i, a in enumerate(new.get("args") or [])]
    for section in ("env", "headers"):
        stored = (old or {}).get(section) or {}
        for k, v in list((new.get(section) or {}).items()):
            if v == SECRET:
                if k in stored:
                    new[section][k] = stored[k]
                else:
                    new[section].pop(k)
    return new


def public_config(cfg: dict) -> dict:
    """The config sent to the browser: secrets masked."""
    out = copy.deepcopy(cfg)
    for section, key in SECRET_PATHS:
        if out.get(section, {}).get(key):
            out[section][key] = SECRET
    out["mcp"]["servers"] = {n: mask_server(s) for n, s in out["mcp"]["servers"].items()}
    out["reverse"]["env"] = mask_server({"env": out["reverse"].get("env") or {}})["env"]
    return out


def strip_masked(update: dict) -> dict:
    """A save from the browser keeps stored secrets when it sends the mask back.

    MCP servers are managed by their own endpoints (a deep merge cannot delete one)."""
    for section, key in SECRET_PATHS:
        if isinstance(update.get(section), dict) and update[section].get(key) == SECRET:
            update[section].pop(key)
    if isinstance(update.get("mcp"), dict):
        update["mcp"].pop("servers", None)
        update["mcp"].pop("approvedProjectServers", None)
    if isinstance(update.get("reverse"), dict) and isinstance(update["reverse"].get("env"), dict):
        update["reverse"]["env"] = {k: v for k, v in update["reverse"]["env"].items() if v != SECRET}
    return update


def remember_project(directory: str) -> None:
    cfg = load_config()
    recent = [directory] + [p for p in cfg.get("recentProjects", []) if p != directory]
    save_config({"recentProjects": recent[:15]})


def permission_mode(cfg: dict, session_data: dict | None = None) -> str:
    """ask | acceptEdits | auto - the session's own choice first, else the global default."""
    default = cfg.get("permissionMode") if cfg.get("permissionMode") in PERMISSION_MODES else ("auto" if cfg.get("autoMode") else "ask")
    if session_data is not None:
        if session_data.get("permissionMode") in PERMISSION_MODES:
            return session_data["permissionMode"]
        if session_data.get("autoMode") is True:
            return "auto"
        if session_data.get("autoMode") is False:
            return default if default != "auto" else "ask"
    return default


def auto_mode(cfg: dict, session_data: dict | None = None) -> bool:
    return permission_mode(cfg, session_data) == "auto"


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
