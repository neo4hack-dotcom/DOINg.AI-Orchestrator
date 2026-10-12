"""savvy-flow engine: plan -> (design) -> delegate -> adversarial review -> close.

The orchestrator role is played by the local LLM (plan, review, close), but the
sequencing is driven here, so the progress bar and agents panel (savvy-progress)
stay exact even when a small local model forgets to report.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from . import llm, prompts
from .agent import AgentSpec, make_context, run_agent
from .config import EFFORT_MAX_STEPS, TIERS, load_config, orchestrator_model, tier_effort, tier_model
from .session import MessageSink, Session, new_id, now_ms
from .tools import IGNORED_DIRS, READ_ONLY, make_diff

TIER_ORDER = ["light", "medium", "careful", "heavy", "fable"]
WORKER_TOOLS = ["read", "list", "glob", "grep", "write", "edit", "bash", "step"]
PHASES = ("plan", "design", "delegate", "review", "close")


# ---------------------------------------------------------------------------
# helpers


def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        obj = llm._loads_lenient(c)
        if obj is not None:
            return obj
    return None


def is_greenfield(directory: str) -> bool:
    root = Path(directory)
    if not root.exists():
        return True
    for p in root.iterdir():
        if p.name in IGNORED_DIRS or p.name.startswith("."):
            continue
        return False
    return True


def _norm_tier(t: str, default: str = "medium") -> str:
    t = (t or "").lower().replace("savvy-", "").strip()
    return t if t in TIERS else default


def normalize_tasks(raw: list) -> list[dict]:
    tasks: list[dict] = []
    seen: set[str] = set()
    for i, t in enumerate(raw or []):
        if isinstance(t, str):
            t = {"title": t, "brief": t}
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or f"Task {i + 1}").strip()[:80]
        base, k = title, 2
        while title.lower() in seen:
            title = f"{base} ({k})"
            k += 1
        seen.add(title.lower())
        n = len(tasks) + 1
        after = []
        for a in t.get("after") or t.get("dependsOn") or []:
            try:
                a = int(a)
            except (TypeError, ValueError):
                continue
            if 0 < a < n and a not in after:  # only earlier tasks: no cycles
                after.append(a)
        tasks.append({
            "n": n,
            "title": title,
            "tier": _norm_tier(str(t.get("tier", ""))),
            "after": after,
            "brief": str(t.get("brief") or t.get("description") or title).strip(),
            "verify": str(t.get("verify") or "").strip(),
            "status": "planned",
            "round": 0,
            "runIds": [],
            "defects": [],
            "note": "",
        })
    return tasks


def changes_diff(directory: str, changes: dict[str, Optional[str]], limit: int = 14000) -> str:
    if not changes:
        return "(the worker changed no files)"
    chunks = []
    for path, before in changes.items():
        p = Path(directory) / path
        after = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        d = make_diff(path, before or "", after)
        chunks.append(d or f"(no net change in {path})\n")
    text = "".join(chunks)
    if len(text) > limit:
        text = text[:limit] + f"\n... [diff truncated, {len(text) - limit} more characters; read the files to see the rest]"
    return text


def _run_public(run: dict) -> dict:
    return {k: v for k, v in run.items() if k != "messages"}


def emit_run(session: Session, run: dict) -> None:
    session.emit("run.updated", {"run": _run_public(run)})
    session.touch()


# ---------------------------------------------------------------------------
# workers


async def run_worker(
    session: Session,
    *,
    tier: str,
    title: str,
    brief: str,
    verify: str = "",
    design: str = "",
    defects: Optional[list[str]] = None,
    round_: int = 0,
    task_n: Optional[int] = None,
    flow_id: Optional[str] = None,
) -> tuple[str, dict, dict]:
    """Run one savvy worker on a self-contained brief. Returns (report, changes, run)."""
    cfg = load_config()
    tier = _norm_tier(tier)
    effort = tier_effort(cfg, tier)
    model = tier_model(cfg, tier)
    max_steps = EFFORT_MAX_STEPS.get(effort, 20)
    run = {
        "id": new_id("run"),
        "type": f"savvy-{tier}",
        "tier": tier,
        "description": title,
        "model": model,
        "effort": effort,
        "status": "running",
        "startedAt": now_ms(),
        "endedAt": None,
        "contextTokens": 0,
        "contextMax": int(cfg["llm"].get("contextWindow") or 16384),
        "tokens": 0,
        "costUsd": 0.0,
        "steps": 0,
        "round": round_,
        "stepDone": None,
        "stepTotal": None,
        "stepNote": "",
        "taskN": task_n,
        "flowId": flow_id,
        "report": "",
        "messages": [],
    }
    session.data.setdefault("runs", []).append(run)
    emit_run(session, run)

    sink = MessageSink(session, run)
    ctx = make_context(session, run["type"], run)

    def on_step(info: dict) -> None:
        run.update(info)
        emit_run(session, run)

    def on_usage(res: llm.ChatResult) -> None:
        run["contextTokens"] = res.prompt_tokens + res.completion_tokens
        run["tokens"] += res.prompt_tokens + res.completion_tokens
        emit_run(session, run)

    def on_tool(_call: llm.ToolCall) -> None:
        run["steps"] += 1
        emit_run(session, run)

    ctx.on_step = on_step
    parts = [f"# Task: {title}", "", brief.strip()]
    if design:
        parts += ["", "## Design specs (decided by the orchestrator, follow them exactly)", design.strip()]
    if verify:
        parts += ["", f"## Verification\n{verify.strip()}"]
    if defects:
        parts += ["", f"## Review round {round_}: the orchestrator returned your previous attempt. Fix these defects:"]
        parts += [f"- {d}" for d in defects]
    prompt = "\n".join(parts)
    sink.add_message("user", "orchestrator", parts=[{"id": new_id("prt"), "type": "text", "text": prompt}])

    spec = AgentSpec(
        name=run["type"],
        system=prompts.base(session.directory, cfg["llm"].get("systemPrompt", "")) + "\n\n" + prompts.worker(tier, effort, max_steps),
        tools=WORKER_TOOLS,
        model=model,
        max_steps=max_steps,
        on_usage=on_usage,
        on_tool=on_tool,
    )
    history = [{"role": "user", "content": prompt}]
    try:
        out = await run_agent(session, spec, history, sink, ctx)
        run["status"] = "done"
        run["report"] = out.text or "(the worker wrote no report)"
    except asyncio.CancelledError:
        run["status"] = "failed"
        run["report"] = "Aborted."
        run["endedAt"] = now_ms()
        emit_run(session, run)
        raise
    except llm.LLMError as e:
        run["status"] = "failed"
        run["report"] = f"Worker failed: {e}"
    run["endedAt"] = now_ms()
    emit_run(session, run)
    return run["report"], dict(ctx.changes), run


# ---------------------------------------------------------------------------
# the orchestrated flow


class FlowEngine:
    def __init__(self, session: Session):
        self.session = session
        self.sink = MessageSink(session)
        self.plan_history: list[dict] = []
        self.review_lock = asyncio.Lock()

    # -- state ----------------------------------------------------------
    @property
    def flow(self) -> dict:
        return self.session.data["flow"]

    def emit(self) -> None:
        self.session.emit("flow.updated", {"flow": self.flow})
        self.session.touch()

    def set_phase(self, phase: str) -> None:
        if self.flow["phase"] != phase:
            self.flow["phase"] = phase
            self.emit()

    def cfg(self) -> dict:
        return load_config()

    def orch_spec(self, system: str, tools: list[str], steps: int) -> AgentSpec:
        cfg = self.cfg()
        return AgentSpec(
            name="savvy-orchestrator",
            system=prompts.base(self.session.directory, cfg["llm"].get("systemPrompt", "")) + "\n\n" + system,
            tools=tools,
            model=orchestrator_model(cfg),
            max_steps=steps,
        )

    def _hide_last_text(self, text: str) -> None:
        """Hide the raw JSON the orchestrator answered with: a card shows it instead."""
        for msg in reversed(self.session.data["messages"]):
            for part in msg["parts"]:
                if part.get("type") == "text" and part.get("text", "").strip() == text.strip():
                    part["hidden"] = True
                    self.sink.update_part(msg, part)
                    return

    async def _json_repair(self, text: str, schema_hint: str) -> Optional[dict]:
        cfg = self.cfg()
        try:
            res = await llm.chat(
                cfg["llm"], model=orchestrator_model(cfg), json_mode=True, temperature=0,
                messages=[
                    {"role": "system", "content": "Convert the input into ONE valid JSON object. Output JSON only."},
                    {"role": "user", "content": f"Expected shape:\n{schema_hint}\n\nInput:\n{text[:12000]}"},
                ],
            )
        except llm.LLMError:
            return None
        return extract_json(res.text)

    # -- entry points ------------------------------------------------------
    async def start(self, request: str) -> None:
        greenfield = is_greenfield(self.session.directory)
        self.session.data["flow"] = {
            "id": new_id("flow"),
            "request": request,
            "title": "savvy-flow",
            "summary": "",
            "total": 0,
            "done": 0,
            "running": 0,
            "phase": "plan",
            "isFinished": False,
            "tasks": [],
            "design": "",
            "touchesDesign": False,
            "greenfield": greenfield,
            "spec": "",
            "questions": [],
            "awaiting": None,
            "interrupted": False,
            "startedAt": now_ms(),
            "endedAt": None,
        }
        self.emit()
        hint = prompts.GREENFIELD if greenfield else ""
        self.plan_history = [{"role": "user", "content": f"Task from the user:\n{request}"}]
        self._plan_system = prompts.ORCH_PLAN.format(greenfield=hint)
        await self.plan()

    async def revise(self, feedback: str) -> None:
        """User answered the orchestrator's questions or asked for plan changes."""
        self.flow["awaiting"] = None
        self.emit()
        self.plan_history.append({"role": "user", "content": f"User feedback on your plan:\n{feedback}\n\nProduce the updated plan JSON."})
        await self.plan()

    async def approve(self) -> None:
        self.flow["awaiting"] = None
        self.emit()
        await self.execute()

    # -- phases -----------------------------------------------------------
    async def plan(self) -> None:
        self.set_phase("plan")
        cfg = self.cfg()
        spec = self.orch_spec(self._plan_system, READ_ONLY, int(cfg["flow"].get("planSteps") or 8))
        out = await run_agent(self.session, spec, self.plan_history, self.sink, make_context(self.session, spec.name))
        plan = extract_json(out.text)
        if not plan or "tasks" not in plan:
            plan = await self._json_repair(out.text, '{"title": "", "summary": "", "touchesDesign": false, "design": "", "spec": "", "questions": [], "tasks": [{"title": "", "tier": "medium", "after": [], "brief": "", "verify": ""}]}')
        if not plan:
            self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "error", "text": "The orchestrator did not return a usable plan. Rephrase the task or try a stronger orchestrator model (Settings > Tiers)."}])
            self.flow.update(isFinished=True, phase="close", endedAt=now_ms(), failed=True)
            self.emit()
            return
        self._hide_last_text(out.text)

        tasks = normalize_tasks(plan.get("tasks") or [])
        questions = [str(q) for q in (plan.get("questions") or []) if str(q).strip()]
        self.flow.update(
            title=str(plan.get("title") or "savvy-flow").strip()[:60],
            summary=str(plan.get("summary") or "").strip(),
            touchesDesign=bool(plan.get("touchesDesign")),
            design=str(plan.get("design") or "").strip(),
            spec=str(plan.get("spec") or "").strip() if self.flow["greenfield"] else "",
            questions=questions,
            tasks=tasks,
            total=len(tasks),
            done=0,
        )
        self.sink.add_message("assistant", "savvy-orchestrator", parts=[{
            "type": "plan",
            "flowId": self.flow["id"],
            "title": self.flow["title"],
            "summary": self.flow["summary"],
            "design": self.flow["design"],
            "spec": self.flow["spec"],
            "questions": questions,
            "tasks": [{k: t[k] for k in ("n", "title", "tier", "after", "brief", "verify")} for t in tasks],
        }])
        if questions or not tasks:
            self.flow["awaiting"] = "answers"
            self.emit()
            return
        if cfg["flow"].get("requirePlanApproval", True):
            self.flow["awaiting"] = "approval"
            self.emit()
            return
        self.emit()
        await self.execute()

    async def execute(self) -> None:
        cfg = self.cfg()
        flow = self.flow
        flow["interrupted"] = False
        if flow["greenfield"]:
            self._write_docs()
        if flow["touchesDesign"]:
            self.set_phase("design")
        self.set_phase("delegate")

        sem = asyncio.Semaphore(int(cfg["flow"].get("maxParallel") or 1))
        tasks = flow["tasks"]
        by_n = {t["n"]: t for t in tasks}
        running: dict[int, asyncio.Task] = {}

        async def guarded(t: dict) -> None:
            async with sem:
                await self.do_task(t)

        try:
            while True:
                for t in tasks:
                    if t["status"] != "planned" or t["n"] in running:
                        continue
                    deps = [by_n[a] for a in t["after"] if a in by_n]
                    if any(d["status"] in ("failed", "blocked") for d in deps):
                        t["status"] = "blocked"
                        t["note"] = "A task it depends on failed."
                        self.emit()
                        continue
                    if all(d["status"] == "accepted" for d in deps):
                        running[t["n"]] = asyncio.create_task(guarded(t))
                if not running:
                    break
                done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
                for n, task in list(running.items()):
                    if task in done:
                        running.pop(n)
                        task.result()  # re-raise worker crashes
        except asyncio.CancelledError:
            for task in running.values():
                task.cancel()
            for t in tasks:
                if t["status"] in ("running", "review"):
                    t["status"] = "planned"
            flow.update(running=0, interrupted=True)
            self.emit()
            raise
        await self.close()

    async def do_task(self, t: dict) -> None:
        cfg = self.cfg()
        flow = self.flow
        max_rounds = int(cfg["flow"].get("maxReturnRounds", 2))
        while True:
            t["status"] = "running"
            flow["running"] += 1
            if flow["phase"] != "review":
                flow["phase"] = "delegate"
            self.emit()
            try:
                report, changes, run = await run_worker(
                    self.session, tier=t["tier"], title=t["title"], brief=t["brief"], verify=t["verify"],
                    design=flow["design"] if flow["touchesDesign"] else "", defects=t["defects"],
                    round_=t["round"], task_n=t["n"], flow_id=flow["id"],
                )
            finally:
                flow["running"] = max(0, flow["running"] - 1)
            t["runIds"].append(run["id"])
            t["status"] = "review"
            self.emit()

            verdict = await self.review(t, report, changes, run)
            if verdict["verdict"] == "accept":
                t["status"] = "accepted"
                t["note"] = verdict.get("note", "")
                flow["done"] = sum(1 for x in flow["tasks"] if x["status"] == "accepted")
                self.set_phase("delegate" if flow["running"] else flow["phase"])
                self.emit()
                if flow["greenfield"]:
                    self._write_docs()
                await self._commit(t)
                return
            if t["round"] >= max_rounds:
                t["status"] = "failed"
                t["note"] = f"Returned {t['round'] + 1} times; still: " + "; ".join(verdict.get("defects") or [])[:300]
                self.emit()
                return
            t["round"] += 1
            t["defects"] = verdict.get("defects") or ["The review rejected the previous attempt."]
            new_tier = _norm_tier(verdict.get("tier", ""), t["tier"])
            if TIER_ORDER.index(new_tier) > TIER_ORDER.index(t["tier"]):
                t["tier"] = new_tier  # return to the same or a higher tier, never lower
            self.emit()

    async def review(self, t: dict, report: str, changes: dict, run: dict) -> dict:
        async with self.review_lock:
            self.set_phase("review")
            cfg = self.cfg()
            card_msg = self.sink.add_message("assistant", "savvy-orchestrator")
            card = self.sink.add_part(card_msg, {
                "type": "review", "taskN": t["n"], "title": t["title"], "tier": t["tier"], "round": t["round"],
                "runId": run["id"], "status": "reviewing", "verdict": None, "defects": [], "note": "",
                "files": list(changes.keys()),
            })
            if run["status"] == "failed":
                verdict = {"verdict": "return", "defects": [run["report"]], "tier": t["tier"], "note": "The worker failed."}
            else:
                content = (
                    f"TASK {t['n']}: {t['title']}  (tier {t['tier']}, round {t['round']})\n\n"
                    f"BRIEF:\n{t['brief']}\n\nVERIFY: {t['verify'] or '(not specified)'}\n\n"
                    f"WORKER REPORT:\n{report}\n\nDIFF OF THE FILES IT CHANGED:\n{changes_diff(self.session.directory, changes)}"
                )
                spec = self.orch_spec(prompts.ORCH_REVIEW, READ_ONLY + ["bash", "edit"], int(cfg["flow"].get("reviewSteps") or 8))
                history = [{"role": "user", "content": content}]
                out = await run_agent(self.session, spec, history, self.sink, make_context(self.session, spec.name))
                verdict = extract_json(out.text) or await self._json_repair(
                    out.text, '{"verdict": "accept|return", "defects": [], "tier": "", "note": ""}'
                ) or {}
                if verdict:
                    self._hide_last_text(out.text)
                v = str(verdict.get("verdict", "")).lower()
                if v not in ("accept", "return", "escalate"):
                    low = out.text.lower()
                    v = "return" if ("return" in low and "accept" not in low) else "accept"
                    verdict.setdefault("note", "Verdict inferred from an unstructured review.")
                verdict["verdict"] = "accept" if v == "escalate" else v
                verdict["defects"] = [str(d) for d in (verdict.get("defects") or []) if str(d).strip()]
            card.update(status="done", verdict=verdict["verdict"], defects=verdict.get("defects", []),
                        note=str(verdict.get("note") or ""))
            self.sink.update_part(card_msg, card)
            return verdict

    async def close(self) -> None:
        flow = self.flow
        self.set_phase("close")
        lines = [f"Request: {flow['request']}", f"Plan: {flow['title']} - {flow['summary']}", ""]
        runs = {r["id"]: r for r in self.session.data.get("runs", [])}
        tiers_used: set[str] = set()
        for t in flow["tasks"]:
            lines.append(f"Task {t['n']} [{t['status']}] {t['title']} (tier {t['tier']}, rounds {t['round'] + 1})")
            if t.get("note"):
                lines.append(f"  review note: {t['note']}")
            for rid in t["runIds"][-1:]:
                r = runs.get(rid)
                if r:
                    tiers_used.add(r["type"])
                    lines.append("  last worker report: " + r["report"][:1500].replace("\n", "\n  "))
        lines.append("")
        lines.append("Tiers that ran: " + (", ".join(sorted(tiers_used)) or "none"))
        spec = self.orch_spec(prompts.ORCH_CLOSE, [], 1)
        history = [{"role": "user", "content": "\n".join(lines)}]
        try:
            await run_agent(self.session, spec, history, self.sink, make_context(self.session, spec.name))
        except llm.LLMError as e:
            self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "error", "text": f"Close summary failed: {e}"}])
        flow.update(isFinished=True, endedAt=now_ms(), running=0)
        self.emit()

    # -- side effects -----------------------------------------------------
    def _write_docs(self) -> None:
        flow = self.flow
        root = Path(self.session.directory)
        docs = root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        if flow.get("spec"):
            spec_file = docs / "SPEC.md"
            if not spec_file.exists():
                spec_file.write_text(flow["spec"].rstrip() + "\n", encoding="utf-8")
        lines = [f"# PLAN: {flow['title']}", "", flow["summary"], ""]
        for t in flow["tasks"]:
            mark = "x" if t["status"] == "accepted" else " "
            after = f" (after {', '.join(map(str, t['after']))})" if t["after"] else ""
            lines.append(f"- [{mark}] **{t['n']}. {t['title']}** - tier `{t['tier']}`{after}")
            lines.append(f"  {t['brief'].replace(chr(10), ' ')}")
            if t["verify"]:
                lines.append(f"  Verify: {t['verify']}")
        (docs / "PLAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def _commit(self, t: dict) -> None:
        if not self.cfg()["flow"].get("gitCommit"):
            return
        root = Path(self.session.directory)
        script = "git add -A && git commit -q -m " + json.dumps(f"savvy-flow: task {t['n']} - {t['title']}")
        if not (root / ".git").exists():
            script = "git init -q && " + script
        proc = await asyncio.create_subprocess_shell(script, cwd=str(root), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()


def resume_engine(session: Session) -> FlowEngine:
    """Rebuild an engine for a flow whose engine is gone (server restart)."""
    engine = FlowEngine(session)
    flow = session.data["flow"]
    engine.plan_history = [{"role": "user", "content": f"Task from the user:\n{flow['request']}"}]
    engine._plan_system = prompts.ORCH_PLAN.format(greenfield=prompts.GREENFIELD if flow.get("greenfield") else "")
    return engine
