"""Traceability: an append-only JSONL journal per session, and a Markdown report.

Every decision that matters is logged with a timestamp: user messages, tool calls
(agent, tool, target, status, duration), permissions, plan, phases, task status,
review verdicts, verify commands and their exit codes, commits, compactions,
retries, snapshots and errors. ``report()`` turns it into a readable audit.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

JOURNAL_DIR = DATA_DIR / "sessions"


def path_for(session_id: str) -> Path:
    return JOURNAL_DIR / f"{session_id}.journal.jsonl"


def log(session: Any, kind: str, **data: Any) -> dict:
    entry = {"ts": int(time.time() * 1000), "kind": kind, **data}
    try:
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        with path_for(session.id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
    session.emit("journal", {"entry": entry})
    return entry


def read(session_id: str, limit: int = 1000, kinds: set[str] | None = None) -> list[dict]:
    p = path_for(session_id)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kinds and e.get("kind") not in kinds:
                continue
            out.append(e)
    return out[-limit:]


def _t(ms: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000)) if ms else "-"


def _dur(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m{s % 60:02d}s" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def report(data: dict) -> str:
    """Markdown audit of a session: flow plan, tasks, verdicts, verifications, files, timeline."""
    lines = [f"# Report: {data.get('title', '')}", ""]
    lines.append(f"- Session: `{data['id']}` · folder `{data['directory']}`")
    tok = data.get("tokens") or {}
    lines.append(f"- Tokens: {tok.get('input', 0)} in / {tok.get('output', 0)} out")
    flow = data.get("flow")
    runs = {r["id"]: r for r in data.get("runs", [])}
    if flow:
        dur = (flow.get("endedAt") or int(time.time() * 1000)) - (flow.get("startedAt") or 0)
        lines += [
            f"- Flow: **{flow.get('title')}** · phase `{flow.get('phase')}` · "
            f"{flow.get('done', 0)}/{flow.get('total', 0)} tasks accepted · duration {_dur(dur)}"
            + (f" · flow tokens {flow.get('tokens', 0)}" if flow.get("tokens") else ""),
            "", "## Request", "", flow.get("request", ""), "",
        ]
        if flow.get("summary"):
            lines += ["## Plan", "", flow["summary"], ""]
        if flow.get("modules"):
            lines += ["### Modules", ""]
            for m in flow["modules"]:
                lines.append(f"- **{m.get('id')}** {m.get('name', '')}: {m.get('goal', '')}"
                             + (f" (after {', '.join(m.get('dependsOn') or [])})" if m.get("dependsOn") else ""))
            lines.append("")
        lines += ["## Tasks", "", "| # | Task | Module | Tier | Size | Status | Rounds | Wave | Duration | Tokens |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for t in flow.get("tasks", []):
            trs = [runs[r] for r in t.get("runIds", []) if r in runs]
            d = sum(((r.get("endedAt") or r.get("startedAt", 0)) - r.get("startedAt", 0)) for r in trs)
            tk = sum(r.get("tokens", 0) for r in trs)
            lines.append(f"| {t['n']} | {t['title']} | {t.get('module', '')} | {t['tier']} | {t.get('size', '')} | "
                         f"{t['status']} | {t.get('round', 0) + 1} | {t.get('wave', '')} | {_dur(d)} | {tk} |")
        lines.append("")
        for t in flow.get("tasks", []):
            lines.append(f"### {t['n']}. {t['title']} — {t['status']}")
            if t.get("files"):
                lines.append(f"Files: {', '.join('`' + f + '`' for f in t['files'])}")
            if t.get("note"):
                lines.append(f"Review: {t['note']}")
            for d in t.get("defects", []):
                lines.append(f"- defect: {d}")
            for rid in t.get("runIds", [])[-1:]:
                r = runs.get(rid)
                if r and r.get("report"):
                    lines += ["", "<details><summary>Last worker report</summary>", "", r["report"], "", "</details>"]
            lines.append("")
    timeline = data.get("_journal") or []
    if timeline:
        lines += ["## Timeline", ""]
        for e in timeline:
            k = e.get("kind")
            desc = {
                "user": lambda e: f"user ({e.get('agent')}): {str(e.get('text', ''))[:120]}",
                "tool": lambda e: f"{e.get('agent')} {e.get('tool')} {e.get('target', '')} → {e.get('status')} ({e.get('ms', 0)} ms)",
                "phase": lambda e: f"phase → {e.get('phase')}",
                "task": lambda e: f"task {e.get('n')} {e.get('title', '')} → {e.get('status')}",
                "verdict": lambda e: f"review task {e.get('n')}: {e.get('verdict')} {('; '.join(e.get('defects') or []))[:200]}",
                "verify": lambda e: f"verify `{e.get('command')}` → exit {e.get('exitCode')}",
                "commit": lambda e: f"git commit: {e.get('message')}",
                "plan": lambda e: f"plan: {e.get('title')} ({e.get('tasks')} tasks, {e.get('modules', 0)} modules)",
                "permission": lambda e: f"permission {e.get('tool')} → {e.get('status')}",
                "compaction": lambda e: f"compaction ({e.get('agent')}): {e.get('before')} → {e.get('after')} tokens",
                "retry": lambda e: f"retry {e.get('attempt')} ({e.get('agent')}): {str(e.get('error', ''))[:120]}",
                "error": lambda e: f"ERROR: {str(e.get('error', ''))[:200]}",
                "snapshot": lambda e: f"snapshot {e.get('label', '')} {str(e.get('tree', ''))[:10]}",
                "revert": lambda e: f"revert: {', '.join(e.get('files') or [])[:200]}",
                "loop": lambda e: f"doom loop guard ({e.get('agent')}): {e.get('tool')}",
            }.get(k, lambda e: json.dumps({x: y for x, y in e.items() if x not in ('ts', 'kind')})[:200])
            lines.append(f"- `{_t(e.get('ts'))}` {desc(e)}")
    return "\n".join(lines) + "\n"
