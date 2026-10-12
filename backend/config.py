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
        "contextWindow": 32768,
        "temperature": 0.2,
        "timeout": 600,
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
    "permissions": {"edit": "allow", "bash": "ask"},
    "flow": {
        "requirePlanApproval": True,
        "maxParallel": 1,
        "maxReturnRounds": 2,
        "gitCommit": False,
        "planSteps": 8,
        "reviewSteps": 8,
    },
    "nexusDbPath": "",
    "language": "auto",
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
    for key in ("edit", "bash"):
        if cfg["permissions"].get(key) not in PERMISSION_VALUES:
            cfg["permissions"][key] = DEFAULT_CONFIG["permissions"][key]
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
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_FILE)
    return cfg


def tier_model(cfg: dict, tier: str) -> str:
    return (cfg["tiers"].get(tier) or {}).get("model") or cfg["llm"]["model"]


def tier_effort(cfg: dict, tier: str) -> str:
    return (cfg["tiers"].get(tier) or {}).get("effort") or "medium"


def orchestrator_model(cfg: dict) -> str:
    return cfg.get("orchestratorModel") or cfg["llm"]["model"]


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
