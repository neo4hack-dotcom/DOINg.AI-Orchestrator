"""Session activity and the professional report (PDF / Markdown, English).

``collect(session)`` gathers everything that happened in a session into one structured
document - requests, every reasoning step (main agent and savvy workers), the savvy
plan, workers, validation (verify commands, test / build / lint runs, reviews), every
tool action, files changed, MCP / skills / mods usage, permissions, issues - and the UI
shows it in the Activity view. ``pdf()`` renders it with reportlab: cover with key
figures, table of contents and PDF outline, numbered sections, tables, code blocks,
running header and footer.
"""
from __future__ import annotations

import io
import re
import textwrap
import time
from pathlib import Path
from typing import Any, Optional

from . import journal
from .config import load_config

TEST_CMD = re.compile(
    r"\b(pytest|py\.test|unittest|tox|nox|vitest|jest|mocha|playwright\s+test|cypress\s+run|go\s+test|cargo\s+(test|check|build|clippy)"
    r"|dotnet\s+(test|build)|mvn\s+(test|verify|package)|gradle\w*\s+(test|build|check)|npm\s+(test|run\s+(test|build|lint|check|typecheck))"
    r"|pnpm\s+(test|run\s+\w+|build|lint)|yarn\s+(test|build|lint)|tsc\b|eslint|ruff\s+check|mypy|pyright|flake8|pylint|make\s+(test|check|build)"
    r"|python\s+-m\s+(pytest|unittest|compileall|py_compile))", re.I)


class ReportError(Exception):
    pass


def _iso(ms: Optional[int]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000)) if ms else ""


def _dur(ms: Optional[int]) -> str:
    if ms is None or ms < 0:
        return "-"
    s = int(ms // 1000)
    if s < 60:
        return f"{s}s" if s else f"{int(ms)}ms"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _target(inp: dict) -> str:
    for k in ("path", "command", "pattern", "url", "description", "query", "name", "tool"):
        if inp.get(k):
            return str(inp[k])
    return ""


def _diff_files(diff: str) -> dict[str, list[int]]:
    """Unified diff -> {path: [additions, deletions]}."""
    out: dict[str, list[int]] = {}
    current = None
    for line in (diff or "").splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current = path[2:] if path.startswith("b/") else path
            if current == "/dev/null":
                current = None
            else:
                out.setdefault(current, [0, 0])
        elif line.startswith("--- "):
            prev = line[4:].strip()
            if prev.startswith("a/"):
                out.setdefault(prev[2:], [0, 0])
                current = prev[2:]
        elif current and line.startswith("+") and not line.startswith("+++"):
            out[current][0] += 1
        elif current and line.startswith("-") and not line.startswith("---"):
            out[current][1] += 1
    return out


# ---------------------------------------------------------------------------
# collection


def collect(session) -> dict:
    d = session.data
    cfg = load_config()
    flow = d.get("flow") or None
    runs = d.get("runs", [])
    run_by_id = {r["id"]: r for r in runs}
    entries = journal.read(session.id, 50000)

    items: list[dict] = []          # unified timeline
    requests, reasoning, answers, tools, notices, reviews, validation = [], [], [], [], [], [], []
    files: dict[str, dict] = {}

    def scan(messages: list[dict], run: Optional[dict]) -> None:
        for m in messages:
            if m.get("reverted"):
                continue
            agent = m.get("agent") or ("user" if m.get("role") == "user" else "assistant")
            for p in m.get("parts", []):
                kind = p.get("type")
                ts = p.get("startedAt") or m.get("createdAt")
                base = {"ts": ts, "agent": agent, "runId": run["id"] if run else None,
                        "runTitle": (run or {}).get("description")}
                if p.get("hidden") and kind != "reasoning":
                    continue
                if m.get("role") == "user" and kind == "text":
                    if run is None:
                        it = {**base, "kind": "request", "text": p.get("text", ""), "command": p.get("command"), "goal": bool(p.get("goal"))}
                        requests.append(it)
                        items.append(it)
                    continue
                if kind == "reasoning" and p.get("text", "").strip() and not p.get("hidden"):
                    it = {**base, "kind": "reasoning", "text": p["text"],
                          "durationMs": (p.get("endedAt") or 0) - (p.get("startedAt") or 0) if p.get("endedAt") and p.get("startedAt") else None}
                    reasoning.append(it)
                    items.append(it)
                elif kind == "text" and p.get("text", "").strip():
                    it = {**base, "kind": "answer", "text": p["text"]}
                    answers.append(it)
                    items.append(it)
                elif kind == "tool":
                    meta = p.get("metadata") or {}
                    inp = p.get("input") or {}
                    ms = (p.get("endedAt") or 0) - (p.get("startedAt") or 0) if p.get("endedAt") and p.get("startedAt") else None
                    it = {**base, "kind": "tool", "tool": p.get("tool"), "title": p.get("title") or "", "target": _target(inp)[:300],
                          "status": p.get("status"), "durationMs": ms, "exitCode": meta.get("exitCode"),
                          "mcp": meta.get("mcp"), "skill": meta.get("skill"), "diagnostics": meta.get("diagnostics"),
                          "blockedBy": meta.get("blockedBy"), "output": (p.get("output") or "")[:1500],
                          "additions": meta.get("additions"), "deletions": meta.get("deletions")}
                    tools.append(it)
                    items.append(it)
                    for path, (a, r) in _diff_files(meta.get("diff", "")).items():
                        f = files.setdefault(path, {"path": path, "additions": 0, "deletions": 0, "edits": 0, "agents": set()})
                        f["additions"] += a
                        f["deletions"] += r
                        f["edits"] += 1
                        f["agents"].add(agent)
                    cmd = str(inp.get("command") or "")
                    if p.get("tool") == "bash" and cmd and TEST_CMD.search(cmd) and p.get("status") in ("done", "error"):
                        validation.append({"ts": ts, "source": f"{agent}" + (f" · {run.get('description')}" if run else ""),
                                           "command": cmd[:300], "exitCode": meta.get("exitCode"), "durationMs": ms,
                                           "ok": meta.get("exitCode") == 0, "kind": "test"})
                elif kind == "review":
                    it = {**base, "kind": "review", "taskN": p.get("taskN"), "title": p.get("title"), "round": p.get("round"),
                          "verdict": p.get("verdict"), "defects": p.get("defects") or [], "note": p.get("note") or "", "tier": p.get("tier")}
                    reviews.append(it)
                    items.append(it)
                elif kind in ("notice", "error", "compaction"):
                    it = {**base, "kind": kind, "text": str(p.get("text") or "")[:3000],
                          **({"before": p.get("before"), "after": p.get("after")} if kind == "compaction" else {})}
                    notices.append(it)
                    items.append(it)
                elif kind == "plan":
                    items.append({**base, "kind": "plan", "title": p.get("title"), "summary": p.get("summary"),
                                  "tasks": len(p.get("tasks") or []), "modules": len(p.get("modules") or [])})

    scan(d.get("messages", []), None)
    for r in runs:
        scan(r.get("messages", []), r)

    # Verification commands (auto-verify, task checks, integration) from the journal.
    seen_verify = set()
    for e in entries:
        if e.get("kind") == "verify":
            key = (e.get("ts"), e.get("command"))
            if key in seen_verify:
                continue
            seen_verify.add(key)
            run = run_by_id.get(e.get("runId") or "")
            validation.append({"ts": e.get("ts"), "source": e.get("agent", "") + (f" · {run.get('description')}" if run else ""),
                               "command": str(e.get("command"))[:300], "exitCode": e.get("exitCode"), "durationMs": None,
                               "ok": e.get("exitCode") == 0, "kind": "verify"})
    # A verify command is also a bash tool call: keep one row per (command, time).
    dedup: dict = {}
    for v in sorted(validation, key=lambda v: (v.get("ts") or 0)):
        key = (v["command"], (v.get("ts") or 0) // 5000)
        if key not in dedup or v["kind"] == "verify":
            dedup[key] = {**dedup.get(key, {}), **{k: x for k, x in v.items() if x is not None}}
    validation = sorted(dedup.values(), key=lambda v: v.get("ts") or 0)

    for e in entries:
        k = e.get("kind")
        if k in ("phase", "plan", "commit", "pr", "approve", "revert", "fork", "goal", "goal_eval", "mod", "skill", "resume",
                 "retry", "loop", "split", "fix", "flow_end", "snapshot", "auto_mode", "mcp", "report", "abort"):
            items.append({"ts": e.get("ts"), "kind": "event", "event": k,
                          "text": ", ".join(f"{a}={b}" for a, b in e.items() if a not in ("ts", "kind"))[:400]})
    items.sort(key=lambda it: it.get("ts") or 0)

    # Tool statistics.
    stats: dict[str, dict] = {}
    for t in tools:
        st = stats.setdefault(t["tool"] or "?", {"tool": t["tool"], "calls": 0, "errors": 0, "ms": 0})
        st["calls"] += 1
        st["errors"] += 1 if t["status"] == "error" else 0
        st["ms"] += t["durationMs"] or 0

    mcp_servers: dict[str, int] = {}
    for t in tools:
        if t.get("mcp"):
            mcp_servers[t["mcp"].get("server", "?")] = mcp_servers.get(t["mcp"].get("server", "?"), 0) + 1

    tasks = []
    if flow:
        for t in flow.get("tasks", []):
            trs = [run_by_id[r] for r in t.get("runIds", []) if r in run_by_id]
            tasks.append({
                "n": t.get("n"), "title": t.get("title"), "module": t.get("module") or "", "tier": t.get("tier"),
                "size": t.get("size"), "complexity": t.get("complexity"), "status": t.get("status"), "rounds": (t.get("round") or 0) + 1,
                "wave": t.get("wave"), "files": t.get("files") or [], "verifyCommand": t.get("verifyCommand") or "",
                "checks": t.get("checks") or [], "note": t.get("note") or "", "defects": t.get("defects") or [],
                "brief": t.get("brief") or "", "after": t.get("after") or [],
                "durationMs": sum(((r.get("endedAt") or r.get("startedAt", 0)) - r.get("startedAt", 0)) for r in trs),
                "tokens": sum(r.get("tokens", 0) for r in trs),
            })

    created, updated = d.get("createdAt"), d.get("updatedAt")
    tok = d.get("tokens") or {}
    ok_checks = sum(1 for v in validation if v.get("ok"))
    data = {
        "generatedAt": int(time.time() * 1000),
        "app": "DOINg.AI SuperCode",
        "overview": {
            "id": d["id"], "title": d.get("title"), "directory": d.get("directory"), "createdAt": created, "updatedAt": updated,
            "durationMs": (updated or 0) - (created or 0), "agent": d.get("agent"), "autoMode": d.get("autoMode"),
            "model": cfg["llm"].get("model"), "provider": cfg["llm"].get("provider"),
            "orchestratorModel": cfg.get("orchestratorModel") or cfg["llm"].get("model"),
            "tokensIn": tok.get("input", 0), "tokensOut": tok.get("output", 0), "compactions": d.get("compactions", 0),
            "requests": len(requests), "reasoningSteps": len(reasoning), "toolCalls": len(tools),
            "toolErrors": sum(1 for t in tools if t["status"] == "error"), "filesChanged": len(files),
            "checks": len(validation), "checksPassed": ok_checks, "workers": len(runs), "reviews": len(reviews),
            "lastAnswer": d.get("lastAnswer") or (answers[-1]["text"][:4000] if answers else ""),
            "goal": d.get("goal"),
        },
        "requests": requests,
        "reasoning": reasoning,
        "answers": answers,
        "plan": None if not flow else {
            "id": flow.get("id"), "title": flow.get("title"), "summary": flow.get("summary"), "request": flow.get("request"),
            "mode": flow.get("mode"), "phase": flow.get("phase"), "done": flow.get("done", 0), "total": flow.get("total", 0),
            "isFinished": flow.get("isFinished"), "failed": flow.get("failed"), "tokens": flow.get("tokens", 0),
            "startedAt": flow.get("startedAt"), "endedAt": flow.get("endedAt"),
            "modules": [{k: m.get(k) for k in ("id", "name", "goal", "dependsOn", "files", "contracts", "verifyCommand")}
                        for m in flow.get("modules", [])],
            "tasks": tasks, "stats": flow.get("stats") or {}, "verifyCommands": flow.get("verifyCommands") or [],
            "integration": flow.get("integration") or [], "design": flow.get("design") or "", "spec": flow.get("spec") or "",
            "notes": flow.get("notes") or [], "pullRequest": flow.get("pullRequest"),
        },
        "workers": [{k: r.get(k) for k in ("id", "type", "tier", "description", "model", "effort", "status", "startedAt", "endedAt",
                                            "steps", "tokens", "round", "taskN", "report", "verify")} for r in runs],
        "validation": validation,
        "reviews": reviews,
        "tools": tools,
        "toolStats": sorted(stats.values(), key=lambda s: -s["calls"]),
        "files": sorted(({**f, "agents": sorted(f["agents"])} for f in files.values()), key=lambda f: f["path"]),
        "permissions": [{k: p.get(k) for k in ("createdAt", "agent", "tool", "category", "status")} | {"target": _target(p.get("input") or {})[:200]}
                        for p in d.get("permissionLog", [])],
        "questions": [{"createdAt": q.get("createdAt"), "agent": q.get("agent"), "questions": [x.get("question") for x in q.get("questions", [])],
                       "answers": q.get("answers"), "status": q.get("status")} for q in d.get("questionLog", [])],
        "extensions": {
            "mcp": [{"server": k, "calls": v} for k, v in sorted(mcp_servers.items())],
            "skills": list(d.get("skillsUsed") or []),
            "mods": [e for e in entries if e.get("kind") == "mod"][-200:],
            "blocked": [t for t in tools if t.get("blockedBy")],
        },
        "issues": [n for n in notices if n["kind"] == "error"] + [
            {"ts": e.get("ts"), "kind": e.get("kind"), "agent": e.get("agent"), "text": str(e.get("error") or e.get("tool") or "")[:500]}
            for e in entries if e.get("kind") in ("error", "retry", "loop")],
        "compactions": [n for n in notices if n["kind"] == "compaction"],
        "pullRequests": d.get("pullRequests", []),
        "commits": [e for e in entries if e.get("kind") == "commit" or (e.get("kind") == "mod" and e.get("event") == "commit")],
        "todos": d.get("todos") or [],
        "timeline": items,
    }
    return data


def filename(session, ext: str) -> str:
    title = re.sub(r"[^A-Za-z0-9._-]+", "-", str(session.data.get("title") or "session")).strip("-")[:50] or "session"
    return f"supercode-report-{title}-{time.strftime('%Y%m%d-%H%M')}.{ext}"


# ---------------------------------------------------------------------------
# Markdown


def _clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n].rstrip() + f"\n... [{len(text) - n} more characters]"


def markdown(data: dict, reasoning: str = "summary") -> str:
    o = data["overview"]
    L = [f"# Session report - {o['title']}", "",
         f"Generated by {data['app']} on {_iso(data['generatedAt'])}.", "",
         "## 1. Overview", "",
         "| Item | Value |", "|---|---|",
         f"| Project | `{o['directory']}` |", f"| Session | `{o['id']}` |",
         f"| Period | {_iso(o['createdAt'])} -> {_iso(o['updatedAt'])} ({_dur(o['durationMs'])}) |",
         f"| Model | {o['model']} ({o['provider']}) |", f"| Tokens | {o['tokensIn']:,} in / {o['tokensOut']:,} out |",
         f"| Requests / reasoning steps / tool calls | {o['requests']} / {o['reasoningSteps']} / {o['toolCalls']} ({o['toolErrors']} errors) |",
         f"| Files changed | {o['filesChanged']} |", f"| Checks passed | {o['checksPassed']} / {o['checks']} |", ""]
    if o.get("lastAnswer"):
        L += ["### Outcome (last answer)", "", _clip(o["lastAnswer"], 3000), ""]
    L += ["## 2. Requests", ""] + [f"- `{_iso(r['ts'])}` {_clip(r['text'], 800)}" for r in data["requests"]] + [""]
    p = data["plan"]
    if p:
        L += ["## 3. Savvy-flow plan", "", f"**{p['title']}** - {p['summary']}", "",
              f"Mode {p['mode']} · {p['done']}/{p['total']} tasks accepted · phase {p['phase']}", ""]
        if p["modules"]:
            L += ["| Module | Goal | Depends on |", "|---|---|---|"] + [
                f"| {m['id']} {m.get('name') or ''} | {m.get('goal') or ''} | {', '.join(m.get('dependsOn') or [])} |" for m in p["modules"]] + [""]
        L += ["| # | Task | Module | Tier | Size | Status | Rounds | Duration |", "|---|---|---|---|---|---|---|---|"] + [
            f"| {t['n']} | {t['title']} | {t['module']} | {t['tier']} | {t['size']} | {t['status']} | {t['rounds']} | {_dur(t['durationMs'])} |"
            for t in p["tasks"]] + [""]
    L += ["## 4. Validation and tests", "", "| Time | Source | Command | Result |", "|---|---|---|---|"] + [
        f"| {_iso(v['ts'])} | {v['source']} | `{v['command']}` | {'PASS' if v.get('ok') else 'FAIL'} (exit {v.get('exitCode')}) |"
        for v in data["validation"]] + [""]
    if data["reviews"]:
        L += ["### Reviews", ""] + [f"- Task {r['taskN']} {r['title']} round {(r.get('round') or 0) + 1}: **{r['verdict']}** {r['note']}"
                                     + "".join(f"\n  - {x}" for x in r["defects"]) for r in data["reviews"]] + [""]
    if reasoning != "none":
        L += ["## 5. Reasoning", ""]
        for r in data["reasoning"]:
            L += [f"### {r['agent']}{(' · ' + r['runTitle']) if r.get('runTitle') else ''} - {_iso(r['ts'])} ({_dur(r.get('durationMs'))})", "",
                  "```text", _clip(r["text"], 2500 if reasoning == "summary" else 200000), "```", ""]
    L += ["## 6. Actions", "", "| Time | Agent | Tool | Target | Status | Duration |", "|---|---|---|---|---|---|"] + [
        f"| {_iso(t['ts'])} | {t['agent']} | {t['tool']} | `{(t['target'] or '')[:80]}` | {t['status']} | {_dur(t['durationMs'])} |"
        for t in data["tools"]] + [""]
    L += ["## 7. Files changed", "", "| File | + | - | Edits |", "|---|---|---|---|"] + [
        f"| `{f['path']}` | {f['additions']} | {f['deletions']} | {f['edits']} |" for f in data["files"]] + [""]
    ext = data["extensions"]
    mcp_used = ", ".join("%s (%s)" % (m["server"], m["calls"]) for m in ext["mcp"]) or "none"
    L += ["## 8. Extensions", "", f"- MCP: {mcp_used}",
          f"- Skills used: {', '.join(ext['skills']) or 'none'}", f"- Mod events: {len(ext['mods'])}, blocked actions: {len(ext['blocked'])}", ""]
    if data["issues"]:
        L += ["## 9. Issues", ""] + [f"- `{_iso(i.get('ts'))}` {i.get('kind')}: {_clip(str(i.get('text') or ''), 300)}" for i in data["issues"]] + [""]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# PDF


FONT_CANDIDATES = {
    "regular": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/Library/Fonts/Arial.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf"],
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
             "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "/Library/Fonts/Arial Bold.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    "italic": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Oblique.ttf",
               "C:/Windows/Fonts/segoeuii.ttf", "C:/Windows/Fonts/ariali.ttf", "/Library/Fonts/Arial Italic.ttf",
               "/System/Library/Fonts/Supplemental/Arial Italic.ttf"],
    "mono": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
             "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf", "/Library/Fonts/Courier New.ttf",
             "/System/Library/Fonts/Supplemental/Courier New.ttf"],
}

_fonts: Optional[dict] = None


def _register_fonts() -> dict:
    """System TrueType fonts (Unicode); reportlab's bundled Vera as the portable fallback."""
    global _fonts
    if _fonts:
        return _fonts
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab

    vera = Path(reportlab.__file__).parent / "fonts"
    fallback = {"regular": vera / "Vera.ttf", "bold": vera / "VeraBd.ttf", "italic": vera / "VeraIt.ttf"}
    out: dict[str, str] = {}
    unicode_ok = True
    for role, paths in FONT_CANDIDATES.items():
        name = f"SC-{role}"
        chosen = next((Path(p) for p in paths if Path(p).is_file()), None)
        if chosen is None and role in fallback and fallback[role].is_file():
            chosen = fallback[role]
        if chosen is None:
            out[role] = "Courier" if role == "mono" else "Helvetica"
            if role == "mono":
                unicode_ok = unicode_ok and False
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(chosen)))
            out[role] = name
        except Exception:  # noqa: BLE001 - unreadable font: standard font
            out[role] = "Courier" if role == "mono" else "Helvetica"
    if out["regular"].startswith("SC-") and out["bold"].startswith("SC-"):
        from reportlab.lib.fonts import addMapping

        addMapping(out["regular"], 0, 0, out["regular"])
        addMapping(out["regular"], 1, 0, out["bold"])
        addMapping(out["regular"], 0, 1, out["italic"] if out["italic"].startswith("SC-") else out["regular"])
        addMapping(out["regular"], 1, 1, out["bold"])
    out["unicode"] = "yes" if unicode_ok else ""
    _fonts = out
    return out


EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D]")
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REPLACE = {"→": "->", "←": "<-", "⇒": "=>", "✓": "OK", "✔": "OK", "✗": "x", "✘": "x", "…": "...", "⏱": "", "⎇": "",
           "●": "*", "↑": "^", "↓": "v"}


def _plain(text: Any, fonts: dict) -> str:
    s = CTRL.sub("", str(text if text is not None else ""))
    s = EMOJI.sub("", s)
    for a, b in REPLACE.items():
        s = s.replace(a, b)
    if fonts["regular"] == "Helvetica":
        s = s.encode("latin-1", "replace").decode("latin-1")
    return s


def _x(text: Any, fonts: dict) -> str:
    """Escape for reportlab Paragraph mini-markup."""
    return _plain(text, fonts).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(text: str, fonts: dict) -> str:
    """A small, safe subset of Markdown for paragraphs: **bold**, `code`, line breaks."""
    s = _x(text, fonts)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", lambda m: f'<font face="{fonts["mono"]}">{m.group(1)}</font>', s)
    return s.replace("\n", "<br/>")


def pdf(data: dict, reasoning: str = "summary", include_tools: bool = True) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, KeepTogether, NextPageTemplate, PageBreak,
                                        PageTemplate, Paragraph, Preformatted, Spacer, Table, TableStyle)
        from reportlab.platypus.tableofcontents import TableOfContents
    except ImportError as e:  # pragma: no cover - reportlab is in requirements.txt
        raise ReportError("PDF export needs reportlab: pip install reportlab") from e

    fonts = _register_fonts()
    F, FB, FM = fonts["regular"], fonts["bold"], fonts["mono"]
    INK = colors.HexColor("#1F2430")
    MUTED = colors.HexColor("#6B7280")
    ACCENT = colors.HexColor("#4F46E5")
    LINE = colors.HexColor("#E5E7EB")
    SOFT = colors.HexColor("#F3F4F6")
    GOOD = colors.HexColor("#047857")
    BAD = colors.HexColor("#B91C1C")
    o = data["overview"]

    ss = getSampleStyleSheet()
    st = {
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName=F, fontSize=9.2, leading=13, textColor=INK, alignment=TA_LEFT),
        "small": ParagraphStyle("small", fontName=F, fontSize=7.8, leading=10, textColor=INK),
        "muted": ParagraphStyle("muted", fontName=F, fontSize=8, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName=F, fontSize=7.6, leading=9.6, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName=FB, fontSize=7.6, leading=9.6, textColor=colors.white),
        "h1": ParagraphStyle("h1", fontName=FB, fontSize=16, leading=20, textColor=INK, spaceBefore=4, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName=FB, fontSize=11.5, leading=15, textColor=ACCENT, spaceBefore=10, spaceAfter=5),
        "h3": ParagraphStyle("h3", fontName=FB, fontSize=9.4, leading=12, textColor=INK, spaceBefore=6, spaceAfter=3),
        "code": ParagraphStyle("code", fontName=FM, fontSize=7, leading=8.8, textColor=INK, backColor=SOFT,
                               borderPadding=(4, 5, 4, 5), leftIndent=4, rightIndent=4, spaceBefore=2, spaceAfter=6),
        "title": ParagraphStyle("title", fontName=FB, fontSize=26, leading=31, textColor=INK),
        "subtitle": ParagraphStyle("subtitle", fontName=F, fontSize=13, leading=18, textColor=MUTED),
        "kpi": ParagraphStyle("kpi", fontName=FB, fontSize=15, leading=18, textColor=INK),
        "kpil": ParagraphStyle("kpil", fontName=F, fontSize=7.5, leading=9, textColor=MUTED),
        "toc1": ParagraphStyle("toc1", fontName=F, fontSize=10, leading=15, leftIndent=6),
        "toc2": ParagraphStyle("toc2", fontName=F, fontSize=8.5, leading=12, leftIndent=20, textColor=MUTED),
    }

    class Doc(BaseDocTemplate):
        def __init__(self, buf):
            super().__init__(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                             title=f"Session report - {_plain(o['title'], fonts)}", author="DOINg.AI SuperCode",
                             subject="Coding session activity report", creator="DOINg.AI SuperCode")
            frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
            self.addPageTemplates([PageTemplate("cover", [frame], onPage=self._cover_bg),
                                   PageTemplate("body", [frame], onPage=self._decorate)])
            self._heads = 0

        def _cover_bg(self, canv, doc):
            w, h = A4
            canv.saveState()
            canv.setFillColor(ACCENT)
            canv.rect(0, h - 9 * mm, w, 9 * mm, stroke=0, fill=1)
            canv.setFillColor(MUTED)
            canv.setFont(F, 7.5)
            canv.drawString(18 * mm, 12 * mm, f"Generated {_iso(data['generatedAt'])} · DOINg.AI SuperCode · local session data")
            canv.restoreState()

        def _decorate(self, canv, doc):
            w, h = A4
            canv.saveState()
            canv.setStrokeColor(LINE)
            canv.setLineWidth(0.6)
            canv.line(18 * mm, h - 13 * mm, w - 18 * mm, h - 13 * mm)
            canv.line(18 * mm, 12 * mm, w - 18 * mm, 12 * mm)
            canv.setFont(FB, 7.5)
            canv.setFillColor(ACCENT)
            canv.drawString(18 * mm, h - 11 * mm, "DOINg.AI SuperCode")
            canv.setFont(F, 7.5)
            canv.setFillColor(MUTED)
            canv.drawRightString(w - 18 * mm, h - 11 * mm, _plain(o["title"], fonts)[:80])
            canv.drawString(18 * mm, 8 * mm, f"Session report · {_iso(data['generatedAt'])}")
            canv.drawRightString(w - 18 * mm, 8 * mm, f"Page {doc.page}")
            canv.restoreState()

        def beforeDocument(self):
            self._heads = 0  # stable bookmark keys on every multiBuild pass

        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph) and flowable.style.name in ("h1", "h2"):
                level = 0 if flowable.style.name == "h1" else 1
                text = flowable.getPlainText()
                if text == "Contents":
                    return
                key = f"h{self._heads}"
                self._heads += 1
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
                self.notify("TOCEntry", (level, text, self.page, key))

    story: list = []
    P = lambda t, s="body": Paragraph(_md_inline(t, fonts) if s in ("body", "small", "cell", "muted") else _x(t, fonts), st[s])  # noqa: E731

    def code(text: str, limit: int = 6000) -> Preformatted:
        text = _plain(text, fonts)
        if len(text) > limit:
            text = text[:limit].rstrip() + f"\n... [{len(text) - limit} more characters]"
        lines = []
        for line in text.splitlines() or [""]:
            lines += textwrap.wrap(line.expandtabs(4), 118, replace_whitespace=False, drop_whitespace=False) or [""]
        return Preformatted("\n".join(lines[:400]) + ("\n..." if len(lines) > 400 else ""), st["code"])

    def table(header: list[str], rows: list[list[Any]], widths: list[float], zebra: bool = True) -> Table:
        body = [[Paragraph(_x(h, fonts), st["cellb"]) for h in header]]
        for r in rows:
            body.append([c if hasattr(c, "wrapOn") else Paragraph(_md_inline(str(c if c is not None else ""), fonts), st["cell"]) for c in r])
        total = sum(widths)
        t = Table(body, colWidths=[w / total * (A4[0] - 36 * mm) for w in widths], repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), INK), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        if zebra:
            style += [("BACKGROUND", (0, i), (-1, i), SOFT) for i in range(2, len(body), 2)]
        t.setStyle(TableStyle(style))
        return t

    def status_cell(ok: Optional[bool], text: str) -> Paragraph:
        color = "#047857" if ok else ("#B91C1C" if ok is False else "#6B7280")
        return Paragraph(f'<font color="{color}"><b>{_x(text, fonts)}</b></font>', st["cell"])

    # -- cover -------------------------------------------------------------
    story += [Spacer(1, 30 * mm), Paragraph("Session Report", st["title"]), Spacer(1, 3 * mm),
              Paragraph(_x(o["title"] or "Untitled session", fonts), st["subtitle"]), Spacer(1, 10 * mm)]
    meta_rows = [["Project folder", o["directory"]], ["Session", o["id"]],
                 ["Period", f"{_iso(o['createdAt'])} -> {_iso(o['updatedAt'])}"],
                 ["Model", f"{o['model']} ({o['provider']})" + (f" · orchestrator {o['orchestratorModel']}" if o["orchestratorModel"] != o["model"] else "")],
                 ["Mode", ("auto (no human in the loop)" if o["autoMode"] else "supervised") + f" · agent {o['agent']}"]]
    mt = Table([[Paragraph(_x(a, fonts), st["muted"]), Paragraph(_x(b, fonts), st["body"])] for a, b in meta_rows],
               colWidths=[35 * mm, A4[0] - 36 * mm - 35 * mm])
    mt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [mt, Spacer(1, 12 * mm)]
    kpis = [(_dur(o["durationMs"]), "session span"), (str(o["requests"]), "requests"), (str(o["toolCalls"]), "actions"),
            (str(o["filesChanged"]), "files changed"), (f"{o['checksPassed']}/{o['checks']}", "checks passed"),
            (f"{(o['tokensIn'] + o['tokensOut']) / 1000:.1f}k", "tokens")]
    kt = Table([[Paragraph(_x(v, fonts), st["kpi"]) for v, _ in kpis], [Paragraph(_x(l, fonts), st["kpil"]) for _, l in kpis]],
               colWidths=[(A4[0] - 36 * mm) / len(kpis)] * len(kpis))
    kt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFB")), ("TOPPADDING", (0, 0), (-1, 0), 8),
                            ("BOTTOMPADDING", (0, 1), (-1, 1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
    story += [kt, NextPageTemplate("body"), PageBreak()]

    toc = TableOfContents()
    toc.levelStyles = [st["toc1"], st["toc2"]]
    story += [Paragraph("Contents", st["h1"]), toc, PageBreak()]

    def h1(text: str) -> None:
        story.append(CondPageBreak(40 * mm))
        story.append(Paragraph(_x(text, fonts), st["h1"]))

    def h2(text: str) -> None:
        story.append(CondPageBreak(25 * mm))
        story.append(Paragraph(_x(text, fonts), st["h2"]))

    n = 0

    def section(title: str) -> None:
        nonlocal n
        n += 1
        h1(f"{n}. {title}")

    # -- 1. executive summary -----------------------------------------------
    section("Executive summary")
    p = data["plan"]
    bullets = [f"{o['requests']} request(s) handled over {_dur(o['durationMs'])}, {o['toolCalls']} actions "
               f"({o['toolErrors']} failed and recovered or reported), {o['filesChanged']} file(s) changed."]
    if p:
        bullets.append(f"savvy-flow **{p['title']}**: {p['done']}/{p['total']} tasks accepted"
                       + (" - finished." if p.get("isFinished") and not p.get("failed") else (" - failed." if p.get("failed") else f" - phase {p['phase']}.")))
    if o["checks"]:
        bullets.append(f"Validation: {o['checksPassed']} of {o['checks']} checks passed"
                       + ("." if o["checksPassed"] == o["checks"] else f"; {o['checks'] - o['checksPassed']} failed (see section Validation)."))
    if data["reviews"]:
        acc = sum(1 for r in data["reviews"] if r["verdict"] == "accept")
        bullets.append(f"Orchestrator reviews: {acc} accepted, {len(data['reviews']) - acc} returned for fixes.")
    ext = data["extensions"]
    if ext["mcp"] or ext["skills"]:
        bullets.append("Extensions: " + "; ".join(x for x in [
            ("MCP " + ", ".join(f"{m['server']} ({m['calls']} calls)" for m in ext["mcp"])) if ext["mcp"] else "",
            ("skills " + ", ".join(ext["skills"])) if ext["skills"] else ""] if x) + ".")
    if data["issues"]:
        bullets.append(f"{len(data['issues'])} issue(s) recorded (errors, retries, loop guards) - see Issues.")
    for b in bullets:
        story.append(Paragraph("• " + _md_inline(b, fonts), st["body"]))
    if o.get("lastAnswer"):
        h2("Outcome (last answer of the agent)")
        story.append(code(o["lastAnswer"], 4000))

    # -- 2. requests -------------------------------------------------------
    section("Requests")
    if data["requests"]:
        story.append(table(["Time", "Request"], [[_iso(r["ts"]), (f"[/{r['command']}] " if r.get("command") else "") + r["text"][:1500]]
                                                  for r in data["requests"]], [22, 78]))
    else:
        story.append(P("No request in this session."))

    # -- 3. plan ------------------------------------------------------------
    if p:
        section("Savvy-flow plan")
        story.append(P(f"**{p['title']}** - {p['summary'] or ''}"))
        story.append(P(f"Mode: {p['mode']} · phase: {p['phase']} · {p['done']}/{p['total']} tasks accepted · flow tokens: {p['tokens']:,}"
                       + (f" · duration {_dur((p.get('endedAt') or data['generatedAt']) - (p.get('startedAt') or data['generatedAt']))}"), "muted"))
        if p["stats"]:
            stats = ", ".join(f"{k}: {v}" for k, v in p["stats"].items() if not isinstance(v, (list, dict)))
            if stats:
                story.append(P(stats, "muted"))
        if p["modules"]:
            h2("Modules")
            story.append(table(["Module", "Goal", "Depends on", "Verify"],
                               [[f"**{m['id']}** {m.get('name') or ''}", m.get("goal") or "", ", ".join(m.get("dependsOn") or []),
                                 f"`{m.get('verifyCommand')}`" if m.get("verifyCommand") else "-"] for m in p["modules"]], [22, 46, 14, 18]))
        h2("Tasks")
        story.append(table(["#", "Task", "Module", "Tier", "Size", "Status", "Rounds", "Duration"],
                           [[t["n"], t["title"], t["module"], t["tier"], f"{t['size'] or ''}{('/' + str(t['complexity'])) if t.get('complexity') else ''}",
                             status_cell(t["status"] == "done" or t["status"] == "accepted", str(t["status"])), t["rounds"], _dur(t["durationMs"])]
                            for t in p["tasks"]], [5, 32, 12, 8, 8, 11, 10, 13]))
        for t in p["tasks"]:
            block = [Paragraph(_x(f"Task {t['n']} - {t['title']}", fonts), st["h3"])]
            if t["files"]:
                block.append(P("Files: " + ", ".join(f"`{f}`" for f in t["files"][:20]), "small"))
            if t["brief"]:
                block.append(P(t["brief"][:1500], "small"))
            if t["note"]:
                block.append(P(f"Review: {t['note']}", "small"))
            for dft in t["defects"][:8]:
                block.append(P(f"- defect: {dft}", "small"))
            story.append(KeepTogether(block[:3]))
            story += block[3:]
        if p["spec"]:
            h2("Specification")
            story.append(code(p["spec"], 6000))
        if p["design"]:
            h2("Design decisions")
            story.append(code(p["design"], 4000))

    # -- 4. execution --------------------------------------------------------
    if data["workers"]:
        section("Execution: savvy workers")
        story.append(table(["Worker", "Task", "Model", "Status", "Steps", "Tokens", "Duration"],
                           [[f"{w['type']}", w.get("description") or "", w.get("model") or "",
                             status_cell(w.get("status") == "done", str(w.get("status"))), w.get("steps") or 0, f"{w.get('tokens') or 0:,}",
                             _dur((w.get("endedAt") or 0) - (w.get("startedAt") or 0) if w.get("endedAt") else None)]
                            for w in data["workers"]], [14, 30, 16, 10, 7, 10, 13]))
        for w in data["workers"]:
            if w.get("report"):
                h2(f"Report - {w.get('description') or w['type']} (round {(w.get('round') or 0) + 1})")
                story.append(code(w["report"], 3000))

    # -- 5. validation --------------------------------------------------------
    section("Validation and tests")
    if data["validation"]:
        story.append(table(["Time", "Source", "Command", "Result"],
                           [[_iso(v["ts"]), v["source"], f"`{v['command']}`",
                             status_cell(v.get("ok"), ("PASS" if v.get("ok") else "FAIL") + f" (exit {v.get('exitCode')})")]
                            for v in data["validation"]], [17, 22, 46, 15]))
    else:
        story.append(P("No verification, test, build or lint command was run in this session."))
    if p and p["integration"]:
        h2("Integration checks")
        story.append(table(["Command", "Result"], [[f"`{r.get('command')}`", status_cell(r.get("exitCode") == 0, f"exit {r.get('exitCode')}")]
                                                   for r in p["integration"]], [80, 20]))
    if data["reviews"]:
        h2("Orchestrator reviews")
        story.append(table(["Task", "Round", "Verdict", "Defects / note"],
                           [[f"{r['taskN']}. {r['title']}", (r.get("round") or 0) + 1, status_cell(r["verdict"] == "accept", str(r["verdict"])),
                             "\n".join(f"- {x}" for x in r["defects"]) + (("\n" if r["defects"] else "") + r["note"] if r["note"] else "")]
                            for r in data["reviews"]], [28, 8, 12, 52]))

    # -- 6. reasoning --------------------------------------------------------
    if reasoning != "none" and data["reasoning"]:
        section("Reasoning")
        story.append(P(f"{len(data['reasoning'])} reasoning step(s) streamed by the model(s)"
                       + (" (each cut to 2,500 characters - export with full reasoning for everything)." if reasoning == "summary" else "."), "muted"))
        for i, r in enumerate(data["reasoning"], 1):
            story.append(Paragraph(_x(f"Step {i} · {r['agent']}" + (f" · {r['runTitle']}" if r.get("runTitle") else "")
                                      + f" · {_iso(r['ts'])} · {_dur(r.get('durationMs'))}", fonts), st["h3"]))
            story.append(code(r["text"], 2500 if reasoning == "summary" else 60000))

    # -- 7. actions ---------------------------------------------------------
    section("Actions")
    if data["toolStats"]:
        story.append(table(["Tool", "Calls", "Errors", "Total time"],
                           [[s["tool"], s["calls"], s["errors"], _dur(s["ms"])] for s in data["toolStats"]], [40, 20, 20, 20]))
    if include_tools and data["tools"]:
        h2("Action log")
        rows = []
        for t in data["tools"][:3000]:
            extra = []
            if t.get("mcp"):
                extra.append(f"MCP {t['mcp'].get('server')}")
            if t.get("blockedBy"):
                extra.append(f"blocked by {t['blockedBy']}")
            if t.get("diagnostics"):
                extra.append(f"{len(t['diagnostics'])} diagnostic(s)")
            target = f"`{t['target'][:140]}`" if t.get("target") else "-"
            rows.append([_iso(t["ts"])[11:], t["agent"], t["tool"], target + (" · " + ", ".join(extra) if extra else ""),
                         status_cell(t["status"] == "done", str(t["status"]) + (f" ({t['exitCode']})" if t.get("exitCode") is not None else "")),
                         _dur(t.get("durationMs"))])
        story.append(table(["Time", "Agent", "Tool", "Target", "Status", "Duration"], rows, [10, 16, 10, 41, 11, 12]))
        if len(data["tools"]) > 3000:
            story.append(P(f"... {len(data['tools']) - 3000} more actions (see the session journal).", "muted"))

    # -- 8. files -------------------------------------------------------------
    section("Files changed")
    if data["files"]:
        story.append(table(["File", "Added", "Removed", "Edits", "By"],
                           [[f"`{f['path']}`", f"+{f['additions']}", f"-{f['deletions']}", f["edits"], ", ".join(f["agents"])[:80]]
                            for f in data["files"]], [48, 10, 10, 8, 24]))
    else:
        story.append(P("No file was changed by the agents."))

    # -- 9. extensions, permissions, questions ---------------------------------
    section("Extensions, permissions and decisions")
    mcp_used = ", ".join("%s (%s calls)" % (m["server"], m["calls"]) for m in ext["mcp"]) or "none"
    story.append(P(f"**MCP servers used:** {mcp_used}"))
    story.append(P(f"**Skills used:** {', '.join(ext['skills']) or 'none'}"))
    if ext["blocked"]:
        story.append(P(f"**Actions blocked by mods:** {len(ext['blocked'])}"))
        story.append(table(["Time", "Tool", "Target", "Blocked by"], [[_iso(t["ts"]), t["tool"], f"`{t['target'][:120]}`", t["blockedBy"]]
                                                                     for t in ext["blocked"]], [18, 12, 52, 18]))
    if ext["mods"]:
        story.append(P(f"**Mod events:** {len(ext['mods'])}"))
    if data["permissions"]:
        h2("Permission decisions")
        story.append(table(["Time", "Agent", "Category", "Tool", "Target", "Decision"],
                           [[_iso(x.get("createdAt")), x.get("agent"), x.get("category"), x.get("tool"), f"`{x.get('target') or ''}`",
                             status_cell(x.get("status") == "allowed", str(x.get("status")))] for x in data["permissions"]], [17, 14, 11, 11, 33, 14]))
    if data["questions"]:
        h2("Questions and answers")
        story.append(table(["Time", "Agent", "Questions", "Answers"],
                           [[_iso(q["createdAt"]), q["agent"], "\n".join(q["questions"] or []), "\n".join(", ".join(a) for a in (q["answers"] or []))]
                            for q in data["questions"]], [17, 14, 40, 29]))
    if data["pullRequests"]:
        h2("Pull requests")
        story.append(table(["Title", "Branch", "Target", "URL"], [[pr.get("title"), pr.get("branch"), pr.get("target"), pr.get("url")]
                                                                  for pr in data["pullRequests"]], [30, 20, 15, 35]))

    # -- 10. issues -------------------------------------------------------------
    if data["issues"] or data["compactions"]:
        section("Issues and context management")
        if data["issues"]:
            story.append(table(["Time", "Kind", "Agent", "Detail"], [[_iso(i.get("ts")), i.get("kind"), i.get("agent") or "", str(i.get("text") or "")[:600]]
                                                                     for i in data["issues"]], [17, 10, 15, 58]))
        if data["compactions"]:
            story.append(P(f"{len(data['compactions'])} context compaction(s): old history summarized to keep the model within its window.", "muted"))

    story.append(Spacer(1, 8 * mm))
    story.append(P("End of report. Generated locally by DOINg.AI SuperCode from the session transcript and journal; "
                   "times are local to the machine that ran the session.", "muted"))

    buf = io.BytesIO()
    doc = Doc(buf)
    try:
        doc.multiBuild(story, maxPasses=6)
    except Exception as e:  # noqa: BLE001
        raise ReportError(f"PDF rendering failed: {type(e).__name__}: {e}") from e
    return buf.getvalue()
