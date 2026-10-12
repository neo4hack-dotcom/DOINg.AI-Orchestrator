"""savvy-flow engine: plan -> (design) -> delegate -> review -> verify -> close.

The orchestrator role is played by the local LLM (plan, review, fix plans, close), but
the sequencing is driven here so the run survives small models, long hours and restarts:

- hierarchical planning: an architect pass (modules + contracts, or tasks for small
  requests), then one cheap JSON call per module, then automatic split of oversized
  tasks; ``planner`` merges tiny chains, serializes file conflicts and computes waves
  and critical-path priorities;
- a DAG scheduler runs ready tasks in parallel (longest chains first), never two tasks
  owning the same files at once;
- each worker verifies its task (verifyCommand) and gets its own failures back before
  the review (auto-fix); a light task whose verification passes is accepted without an
  LLM review; returns escalate tier; a last attempt goes one tier up;
- module gates and a final integration phase run the project-wide checks and plan fix
  tasks when they fail;
- token budget, per-task timeout, shared notes between workers, git snapshots per task,
  journal + Markdown report, automatic resume after a server restart.
"""
from __future__ import annotations

import asyncio
import glob
import json
import re
from pathlib import Path
from typing import Optional

from . import journal, llm, permissions, planner, prompts, repomap
from .agent import AgentSpec, execute_tool, make_context, run_agent
from .config import EFFORT_MAX_STEPS, auto_mode, load_config, orchestrator_model, tier_effort, tier_model
from .session import MessageSink, Session, new_id, now_ms
from .snapshot import Snapshots
from .osutil import read_text, run_command, write_text
from .tools import EDIT_TOOLS, IGNORED_DIRS, READ_ONLY, make_diff

WORKER_TOOLS = READ_ONLY + EDIT_TOOLS + ["bash", "webfetch", "notes", "step"]
PHASES = ("plan", "design", "delegate", "review", "verify", "close")
EXIT_RE = re.compile(r"\[exit code (-?\d+)\]\s*$")


# ---------------------------------------------------------------------------
# helpers


def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start: end + 1])
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


def normalize_tasks(raw: list) -> list[dict]:
    """Flat task list -> finalized plan (kept for callers that plan without modules)."""
    cleaned = [t for t in (planner.clean_task(r, i) for i, r in enumerate(raw or [])) if t]
    tasks, _ = planner.finalize(planner.assemble([(None, cleaned)]), merge=False)
    return tasks


def changes_diff(directory: str, changes: dict[str, Optional[str]], limit: int = 14000) -> str:
    if not changes:
        return "(the worker changed no files)"
    chunks = []
    for path, before in changes.items():
        p = Path(directory) / path
        after = read_text(p) if p.exists() else ""
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


def _tail(text: str, n: int = 3000) -> str:
    return text if len(text) <= n else "...\n" + text[-n:]


async def run_check(session: Session, sink: MessageSink, spec: AgentSpec, ctx, command: str, timeout: int) -> dict:
    """Run a verification command as a visible bash tool call (permissions + journal apply)."""
    call = llm.ToolCall(id=new_id("call"), name="bash", arguments={"command": command, "timeout": timeout})
    msg = sink.add_message("assistant", spec.name, spec.model)
    output, _ok = await execute_tool(session, sink, msg, spec, ctx, call, 6000)
    m = EXIT_RE.search(output)
    code = int(m.group(1)) if m else (1 if output.startswith(("Error", "Permission denied", "The user rejected")) else 0)
    journal.log(session, "verify", command=command, exitCode=code, agent=spec.name, runId=sink.run_id)
    return {"command": command, "exitCode": code, "output": _tail(output, 2500)}


# ---------------------------------------------------------------------------
# workers


async def run_worker(
    session: Session,
    *,
    tier: str,
    title: str,
    brief: str,
    verify: str = "",
    verify_command: str = "",
    design: str = "",
    defects: Optional[list[str]] = None,
    round_: int = 0,
    task_n: Optional[int] = None,
    flow_id: Optional[str] = None,
    files: Optional[list[str]] = None,
    context: str = "",
    notes_fn=None,
    on_tokens=None,
) -> tuple[str, dict, dict, list[dict]]:
    """Run one savvy worker on a self-contained brief. Returns (report, changes, run, verify results)."""
    cfg = load_config()
    tier = planner.norm_tier(tier)
    effort = tier_effort(cfg, tier)
    model = tier_model(cfg, tier)
    max_steps = EFFORT_MAX_STEPS.get(effort, 20)
    run = {
        "id": new_id("run"), "type": f"savvy-{tier}", "tier": tier, "description": title, "model": model,
        "effort": effort, "status": "running", "startedAt": now_ms(), "endedAt": None, "contextTokens": 0,
        "contextMax": int(cfg["llm"].get("contextWindow") or 16384), "tokens": 0, "costUsd": 0.0, "steps": 0,
        "round": round_, "stepDone": None, "stepTotal": None, "stepNote": "", "taskN": task_n, "flowId": flow_id,
        "report": "", "verify": [], "messages": [],
    }
    session.data.setdefault("runs", []).append(run)
    emit_run(session, run)

    sink = MessageSink(session, run)
    ctx = make_context(session, run["type"], run)
    ctx.notes = notes_fn

    def on_step(info: dict) -> None:
        run.update(info)
        emit_run(session, run)

    def on_usage(res: llm.ChatResult) -> None:
        if res.completion_tokens or res.text:
            run["contextTokens"] = res.prompt_tokens + res.completion_tokens
        run["tokens"] += res.prompt_tokens + res.completion_tokens
        if on_tokens:
            on_tokens(res.prompt_tokens + res.completion_tokens)
        emit_run(session, run)

    def on_tool(_call: llm.ToolCall) -> None:
        run["steps"] += 1
        emit_run(session, run)

    ctx.on_step = on_step
    parts = [f"# Task: {title}", "", brief.strip()]
    if files:
        parts += ["", "## Files this task owns", ", ".join(f"`{f}`" for f in files)]
    if context:
        parts += ["", context.strip()]
    if design:
        parts += ["", "## Design specs (decided by the orchestrator, follow them exactly)", design.strip()]
    if verify or verify_command:
        parts += ["", "## Verification", verify.strip()] + ([f"Command that must exit 0: `{verify_command}`"] if verify_command else [])
    if defects:
        parts += ["", f"## Review round {round_}: your previous attempt was returned. Fix these defects:"]
        parts += [f"- {d}" for d in defects]
        parts += ["The files already contain the previous attempt: inspect them before editing."]
    prompt = "\n".join(parts)
    sink.add_message("user", "orchestrator", parts=[{"id": new_id("prt"), "type": "text", "text": prompt}])

    spec = AgentSpec(
        name=run["type"],
        system=prompts.system(session.directory, cfg["llm"].get("systemPrompt", ""), prompts.worker(tier, effort, max_steps),
                              auto=auto_mode(cfg, session.data)),
        tools=WORKER_TOOLS if notes_fn else [t for t in WORKER_TOOLS if t != "notes"],
        model=model, max_steps=max_steps, on_usage=on_usage, on_tool=on_tool, pinned=1,
    )
    history = [{"role": "user", "content": prompt}]
    results: list[dict] = []
    try:
        out = await run_agent(session, spec, history, sink, ctx)
        report = out.text
        fixes = int(cfg["flow"].get("autoFixRetries", 2)) if cfg["flow"].get("autoVerify", True) else 0
        attempt = 0
        while verify_command and cfg["flow"].get("autoVerify", True):
            res = await run_check(session, sink, spec, ctx, verify_command, int(cfg["verify"].get("timeout") or 300))
            results.append(res)
            if res["exitCode"] == 0 or attempt >= fixes:
                break
            attempt += 1
            history.append({"role": "user", "content": (
                f"Automatic verification failed (attempt {attempt}/{fixes}): `{verify_command}` exited {res['exitCode']}.\n"
                f"{res['output']}\n\nFix the cause, re-check, then write your final report again.")})
            spec.max_steps = max(6, max_steps // 2)
            out = await run_agent(session, spec, history, sink, ctx)
            report = out.text or report
        run["status"] = "done"
        run["report"] = report or "(the worker wrote no report)"
    except asyncio.CancelledError:
        run.update(status="failed", report="Aborted.", endedAt=now_ms())
        emit_run(session, run)
        raise
    except llm.LLMError as e:
        run["status"] = "failed"
        run["report"] = f"Worker failed: {e}"
        journal.log(session, "error", agent=run["type"], runId=run["id"], error=str(e)[:300])
    run["verify"] = [{k: v for k, v in r.items() if k != "output"} for r in results]
    run["endedAt"] = now_ms()
    emit_run(session, run)
    return run["report"], dict(ctx.changes), run, results


# ---------------------------------------------------------------------------
# the orchestrated flow


class FlowEngine:
    def __init__(self, session: Session):
        self.session = session
        self.sink = MessageSink(session)
        self.plan_history: list[dict] = []
        self.review_lock = asyncio.Lock()
        self._plan_system = ""

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
            journal.log(self.session, "phase", phase=phase)
            self.emit()

    def cfg(self) -> dict:
        return load_config()

    def add_tokens(self, n: int) -> None:
        self.flow["tokens"] = self.flow.get("tokens", 0) + int(n)

    def orch_spec(self, role: str, tools: list[str], steps: int) -> AgentSpec:
        cfg = self.cfg()

        def on_usage(res: llm.ChatResult) -> None:
            self.add_tokens(res.prompt_tokens + res.completion_tokens)

        return AgentSpec(
            name="savvy-orchestrator",
            system=prompts.system(self.session.directory, cfg["llm"].get("systemPrompt", ""), role,
                                  auto=auto_mode(cfg, self.session.data)),
            tools=tools, model=orchestrator_model(cfg), max_steps=steps, on_usage=on_usage, pinned=1,
        )

    def notice(self, text: str) -> None:
        self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "notice", "text": text}])

    def _hide_last_text(self, text: str) -> None:
        """Hide the raw JSON the orchestrator answered with: a card shows it instead."""
        for msg in reversed(self.session.data["messages"]):
            for part in msg["parts"]:
                if part.get("type") == "text" and part.get("text", "").strip() == text.strip():
                    part["hidden"] = True
                    self.sink.update_part(msg, part)
                    return

    async def _json_call(self, role: str, content: str, schema_hint: str, visible: str) -> Optional[dict]:
        """A tool-less JSON call to the orchestrator model, retried and repaired."""
        spec = self.orch_spec(role, [], 1)
        history = [{"role": "user", "content": content}]
        self.notice(visible)
        try:
            out = await run_agent(self.session, spec, history, self.sink, make_context(self.session, spec.name), json_mode=True)
        except llm.LLMError as e:
            journal.log(self.session, "error", error=f"{visible}: {e}")
            return None
        data = extract_json(out.text) or await self._json_repair(out.text, schema_hint)
        if data:
            self._hide_last_text(out.text)
        return data

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
        self.add_tokens(res.prompt_tokens + res.completion_tokens)
        return extract_json(res.text)

    def notes(self, action: str, text: str, author: str = "", task: Optional[int] = None) -> list:
        notes = self.flow.setdefault("notes", [])
        if action == "add" and text:
            notes.append({"author": author, "task": task, "text": text[:400], "ts": now_ms()})
            journal.log(self.session, "note", author=author, task=task, text=text[:400])
            self.emit()
        return notes

    # -- entry points ------------------------------------------------------
    async def start(self, request: str) -> None:
        greenfield = is_greenfield(self.session.directory)
        self.session.data["flow"] = {
            "id": new_id("flow"), "request": request, "title": "savvy-flow", "summary": "", "mode": "simple",
            "total": 0, "done": 0, "running": 0, "phase": "plan", "isFinished": False, "tasks": [], "modules": [],
            "design": "", "touchesDesign": False, "greenfield": greenfield, "spec": "", "questions": [],
            "verifyCommands": [], "awaiting": None, "interrupted": False, "executing": False, "startedAt": now_ms(),
            "endedAt": None, "tokens": 0, "stats": {}, "notes": [], "integrationRound": 0, "resumeCount": 0,
            "baseSnapshot": None,
        }
        journal.log(self.session, "flow", request=request[:2000], greenfield=greenfield)
        self.emit()
        cfg = self.cfg()
        content = f"Task from the user:\n{request}"
        if not greenfield and cfg["context"].get("repoMap", True):
            rmap = await asyncio.to_thread(repomap.build, self.session.directory, int(cfg["context"].get("repoMapChars") or 6000))
            if rmap:
                content += f"\n\nRepo map (most referenced files and their signatures):\n{rmap}"
        self.plan_history = [{"role": "user", "content": content}]
        self._plan_system = prompts.orch_plan(greenfield)
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

    # -- planning -----------------------------------------------------------
    async def plan(self) -> None:
        self.set_phase("plan")
        cfg = self.cfg()
        spec = self.orch_spec(self._plan_system, READ_ONLY, int(cfg["flow"].get("planSteps") or 8))
        out = await run_agent(self.session, spec, self.plan_history, self.sink, make_context(self.session, spec.name))
        plan = extract_json(out.text)
        if not plan or not ("tasks" in plan or "modules" in plan or "questions" in plan):
            plan = await self._json_repair(out.text, '{"title": "", "summary": "", "mode": "simple", "touchesDesign": false, "design": "", "spec": "", "questions": [], "verifyCommands": [], "modules": [], "tasks": [{"title": "", "tier": "medium", "size": "M", "complexity": 3, "files": [], "after": [], "brief": "", "verify": "", "verifyCommand": ""}]}')
        if not plan:
            self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "error", "text": "The orchestrator did not return a usable plan. Rephrase the task or try a stronger orchestrator model (Settings > Tiers)."}])
            self.flow.update(isFinished=True, phase="close", endedAt=now_ms(), failed=True)
            self.emit()
            return
        self._hide_last_text(out.text)

        questions = [str(q) for q in (plan.get("questions") or []) if str(q).strip()]
        auto = auto_mode(cfg, self.session.data)
        if questions and auto:
            self._auto_rounds = getattr(self, "_auto_rounds", 0) + 1
            journal.log(self.session, "question", agent="savvy-orchestrator", questions=questions, auto=True)
            if self._auto_rounds <= 2:
                self.notice("Auto mode: the orchestrator answers its own questions with stated assumptions.")
                self.plan_history.append({"role": "user", "content": (
                    "AUTO MODE: no user is available to answer. Answer your own questions with the most reasonable "
                    "assumptions, list those assumptions in the summary, and return the complete plan JSON with an "
                    "empty questions list.\nYour questions were:\n" + "\n".join(f"- {q}" for q in questions))})
                await self.plan()
                return
            questions = []  # still asking: proceed with what was planned
        modules = planner.clean_modules(plan.get("modules") or [])
        hierarchical = bool(cfg["flow"].get("hierarchical", True))
        mode = "modular" if modules and hierarchical else "simple"
        self.flow.update(
            title=str(plan.get("title") or "savvy-flow").strip()[:60],
            summary=str(plan.get("summary") or "").strip(),
            touchesDesign=bool(plan.get("touchesDesign")),
            design=str(plan.get("design") or "").strip(),
            spec=str(plan.get("spec") or "").strip() if self.flow["greenfield"] else "",
            questions=questions, mode=mode, modules=modules,
            verifyCommands=[str(c).strip() for c in (plan.get("verifyCommands") or []) if str(c).strip()][:6],
        )
        self.emit()

        module_tasks: list[tuple[Optional[dict], list[dict]]] = []
        if questions:
            pass
        elif mode == "modular":
            for i, module in enumerate(modules, 1):
                items = await self.plan_module(module, i, len(modules))
                module_tasks.append((module, await self.split_pass(items)))
        else:
            raw = plan.get("tasks") or []
            if not raw and modules:  # hierarchical disabled: one task per module
                raw = [{"title": m["name"], "brief": f"{m['goal']}\n\nContracts:\n{m['contracts']}", "files": m["files"],
                        "verifyCommand": m["verifyCommand"], "size": "L", "complexity": 3} for m in modules]
            cleaned = [t for t in (planner.clean_task(r, i) for i, r in enumerate(raw)) if t]
            module_tasks.append((None, await self.split_pass(cleaned)))

        tasks, stats = planner.finalize(planner.assemble(module_tasks), merge=bool(cfg["flow"].get("mergeSmallTasks", True))) \
            if module_tasks else ([], {})
        self.flow.update(tasks=tasks, total=len(tasks), done=0, stats=stats)
        journal.log(self.session, "plan", title=self.flow["title"], tasks=len(tasks), modules=len(modules), mode=mode, stats=stats)
        self.sink.add_message("assistant", "savvy-orchestrator", parts=[{
            "type": "plan", "flowId": self.flow["id"], "title": self.flow["title"], "summary": self.flow["summary"],
            "design": self.flow["design"], "spec": self.flow["spec"], "questions": questions, "mode": mode,
            "modules": [{k: m[k] for k in ("id", "name", "goal", "dependsOn", "files")} for m in modules],
            "stats": stats, "verifyCommands": self.flow["verifyCommands"],
            "tasks": [{k: t.get(k) for k in ("n", "title", "tier", "size", "complexity", "files", "after", "module",
                                             "wave", "priority", "brief", "verify", "verifyCommand", "merged")} for t in tasks],
        }])
        if not tasks and auto:
            self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "error", "text": "Auto mode: the orchestrator produced no task. Rephrase the request or use a stronger orchestrator model."}])
            self.flow.update(isFinished=True, phase="close", endedAt=now_ms(), failed=True)
            self.emit()
            return
        if questions or not tasks:
            self.flow["awaiting"] = "answers"
            self.emit()
            return
        if cfg["flow"].get("requirePlanApproval", True) and not auto:
            self.flow["awaiting"] = "approval"
            self.emit()
            return
        self.emit()
        await self.execute()

    def _module_context(self, module: dict) -> str:
        by_id = {m["id"]: m for m in self.flow.get("modules", [])}
        lines = [f"## Module {module['id']}: {module['name']}", module["goal"]]
        if module.get("contracts"):
            lines += ["", "Contracts this module exposes:", module["contracts"]]
        for dep in module.get("dependsOn", []):
            d = by_id.get(dep)
            if d and d.get("contracts"):
                lines += ["", f"Contracts of module {d['id']} ({d['name']}) you may rely on:", d["contracts"]]
        return "\n".join(lines)

    async def plan_module(self, module: dict, i: int, total: int) -> list[dict]:
        others = "\n".join(f"- {m['id']}: {m['name']} (files: {', '.join(m['files']) or '-'})"
                           for m in self.flow["modules"] if m["id"] != module["id"])
        content = (
            f"Overall request:\n{self.flow['request'][:3000]}\n\nPlan summary: {self.flow['summary']}\n\n"
            + (f"SPEC (excerpt):\n{self.flow['spec'][:3000]}\n\n" if self.flow.get("spec") else "")
            + (f"Design specs:\n{self.flow['design'][:2000]}\n\n" if self.flow.get("design") else "")
            + f"Other modules (not yours):\n{others or '-'}\n\n"
            + self._module_context(module)
            + f"\n\nFiles this module owns: {', '.join(module['files']) or '(decide)'}\n"
            + (f"Module check command: {module['verifyCommand']}\n" if module.get("verifyCommand") else "")
        )
        data = await self._json_call(prompts.orch_module(), content, '{"tasks": [...]}',
                                     f"Planning module {i}/{total}: {module['name']}")
        raw = (data or {}).get("tasks") or []
        items = [t for t in (planner.clean_task(r, k) for k, r in enumerate(raw)) if t]
        if not items:  # the module call failed: one task for the whole module
            items = [planner.clean_task({"title": module["name"], "brief": f"{module['goal']}\n\n{self._module_context(module)}",
                                         "files": module["files"], "size": "L", "complexity": 3,
                                         "verifyCommand": module.get("verifyCommand", "")}, 0)]
        ctx = self._module_context(module)
        for t in items:
            t["module"] = module["id"]
            t["context"] = ctx
            if not t["files"]:
                t["files"] = []
        return items

    async def split_pass(self, items: list[dict]) -> list[dict]:
        """Split oversized tasks into homogeneous subtasks. ``after`` entries become titles."""
        cfg = self.cfg()
        # Local indexes -> titles so insertions do not shift references.
        titles = [t["title"] for t in items]
        for t in items:
            t["after"] = [titles[int(a) - 1] if (isinstance(a, int) or str(a).isdigit()) and 0 < int(a) <= len(titles) else a
                          for a in t["after"]]
        if not cfg["flow"].get("splitLargeTasks", True):
            return items
        out: list[dict] = []
        splits = 0
        for t in items:
            if not planner.needs_split(t) or splits >= 8:
                out.append(t)
                continue
            data = await self._json_call(prompts.orch_split(), f"Task to split:\n{json.dumps({k: t[k] for k in ('title', 'brief', 'files', 'verify', 'verifyCommand', 'tier', 'complexity')}, ensure_ascii=False)}",
                                         '{"tasks": [...]}', f"Splitting oversized task: {t['title']}")
            subs = [s for s in (planner.clean_task(r, k) for k, r in enumerate((data or {}).get("tasks") or [])) if s]
            if len(subs) < 2:
                out.append(t)
                continue
            splits += 1
            sub_titles = [f"{s['title']}" for s in subs]
            for k, s in enumerate(subs):
                local = [sub_titles[int(a) - 1] for a in s["after"] if str(a).isdigit() and 0 < int(a) <= k]
                s["after"] = (t["after"] if not local else []) + local
                s["module"] = t.get("module", "")
                s["context"] = t.get("context", "")
                s["files"] = s["files"] or t["files"]
            # Whoever waited for the big task now waits for all of its parts.
            for other in items:
                if t["title"] in other["after"]:
                    other["after"] = [x for x in other["after"] if x != t["title"]] + sub_titles
            out.extend(subs)
            journal.log(self.session, "split", task=t["title"], into=sub_titles)
        return out

    # -- execution ----------------------------------------------------------
    async def execute(self) -> None:
        cfg = self.cfg()
        flow = self.flow
        flow.update(interrupted=False, executing=True)
        self._grant_plan_commands()
        self.emit()
        if not flow.get("baseSnapshot"):
            flow["baseSnapshot"] = await Snapshots(self.session.directory).track()
            if not self.session.data.get("baseSnapshot"):
                self.session.data["baseSnapshot"] = flow["baseSnapshot"]
        if flow["greenfield"] or flow.get("mode") == "modular":
            self._write_docs()
        if flow["touchesDesign"] and flow["phase"] == "plan":
            self.set_phase("design")
        try:
            while True:
                await self._run_dag()
                if flow.get("budgetExceeded"):
                    break
                if not await self._integration():
                    break
        except asyncio.CancelledError:
            for t in flow["tasks"]:
                if t["status"] in ("running", "review"):
                    t["status"] = "planned"
                    t["interruptedAttempt"] = True
            flow.update(running=0, interrupted=True, executing=False)
            self.emit()
            raise
        await self.close()

    def _grant_plan_commands(self) -> None:
        """Approving the plan approves the verification commands it lists (no prompt per check)."""
        flow = self.flow
        commands = list(flow.get("verifyCommands", [])) + self.cfg()["verify"].get("commands", [])
        commands += [t.get("verifyCommand", "") for t in flow["tasks"]] + [m.get("verifyCommand", "") for m in flow.get("modules", [])]
        grants = self.session.data.setdefault("alwaysAllow", [])
        for cmd in commands:
            for sub in permissions.split_commands(cmd or ""):
                pattern = glob.escape(sub)
                if pattern not in grants:
                    grants.append(pattern)

    async def _run_dag(self) -> None:
        cfg = self.cfg()
        flow = self.flow
        sem = asyncio.Semaphore(int(cfg["flow"].get("maxParallel") or 1))
        running: dict[int, asyncio.Task] = {}

        async def guarded(t: dict) -> None:
            async with sem:
                if self._over_budget():
                    t["status"] = "planned"
                    return
                await self.do_task(t)

        try:
            while True:
                tasks = flow["tasks"]
                by_n = {t["n"]: t for t in tasks}
                ready = []
                for t in tasks:
                    if t["status"] != "planned" or t["n"] in running:
                        continue
                    deps = [by_n[a] for a in t["after"] if a in by_n]
                    if any(d["status"] in ("failed", "blocked") for d in deps):
                        t["status"] = "blocked"
                        t["note"] = "A task it depends on failed."
                        journal.log(self.session, "task", n=t["n"], title=t["title"], status="blocked")
                        self.emit()
                        continue
                    if all(d["status"] == "accepted" for d in deps):
                        ready.append(t)
                if self._over_budget():
                    if not flow.get("budgetExceeded"):
                        flow["budgetExceeded"] = True
                        self.notice(f"Token budget reached ({flow.get('tokens', 0)} tokens): no new task is started.")
                        journal.log(self.session, "error", error="token budget reached")
                        self.emit()
                    ready = []
                ready.sort(key=lambda x: (-x.get("priority", 0), x["n"]))  # critical path first
                for t in ready:
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
            await asyncio.gather(*running.values(), return_exceptions=True)
            raise

    def _over_budget(self) -> bool:
        budget = int(self.cfg()["flow"].get("tokenBudget") or 0)
        return bool(budget) and self.flow.get("tokens", 0) >= budget

    def _module(self, mid: str) -> Optional[dict]:
        return next((m for m in self.flow.get("modules", []) if m["id"] == mid), None)

    def _set_status(self, t: dict, status: str) -> None:
        t["status"] = status
        journal.log(self.session, "task", n=t["n"], title=t["title"], status=status, tier=t["tier"], round=t["round"])
        self.emit()

    async def do_task(self, t: dict) -> None:
        cfg = self.cfg()
        flow = self.flow
        max_rounds = int(cfg["flow"].get("maxReturnRounds", 2))
        timeout = max(1, int(cfg["flow"].get("taskTimeoutMinutes") or 45)) * 60
        snaps = Snapshots(self.session.directory)
        while True:
            t["snapshot"] = t.get("snapshot") or await snaps.track()
            t["startedAt"] = t.get("startedAt") or now_ms()
            self._set_status(t, "running")
            flow["running"] += 1
            if flow["phase"] not in ("review",):
                self.set_phase("delegate")
            self.emit()
            defects = list(t["defects"])
            if t.pop("interruptedAttempt", False):
                defects.append("A previous attempt was interrupted (server restart or abort): files may be partially changed. Inspect them first.")
            module = self._module(t.get("module", ""))
            try:
                report, changes, run, checks = await asyncio.wait_for(run_worker(
                    self.session, tier=t["tier"], title=t["title"], brief=t["brief"], verify=t["verify"],
                    verify_command=t.get("verifyCommand", ""), design=flow["design"] if flow["touchesDesign"] else "",
                    defects=defects, round_=t["round"], task_n=t["n"], flow_id=flow["id"], files=t.get("files"),
                    context=(t.get("context") or (self._module_context(module) if module else "")) + self._notes_digest(),
                    notes_fn=lambda action, text, _t=t: self.notes(action, text, f"task {_t['n']}", _t["n"]),
                    on_tokens=self.add_tokens,
                ), timeout=timeout)
            except asyncio.TimeoutError:
                report, changes, checks = f"Timed out after {timeout // 60} minutes.", {}, []
                run = next((r for r in reversed(self.session.data["runs"]) if r.get("taskN") == t["n"]), {"id": "", "status": "failed", "report": report})
                if run.get("status") == "running":
                    run.update(status="failed", report=report, endedAt=now_ms())
                    emit_run(self.session, run)
            finally:
                flow["running"] = max(0, flow["running"] - 1)
            if run.get("id"):
                t["runIds"].append(run["id"])
            t["checks"] = [{k: v for k, v in c.items() if k != "output"} for c in checks]
            self._set_status(t, "review")

            passed = bool(checks) and checks[-1]["exitCode"] == 0
            if run.get("status") == "done" and passed and t["tier"] == "light" and cfg["flow"].get("fastAcceptLight", True):
                verdict = {"verdict": "accept", "defects": [], "note": f"Auto-accepted: `{checks[-1]['command']}` passed.", "auto": True}
                self._review_card(t, run, changes, verdict)
            else:
                verdict = await self.review(t, report, changes, run, checks)
            journal.log(self.session, "verdict", n=t["n"], title=t["title"], verdict=verdict["verdict"],
                        defects=verdict.get("defects", []), auto=bool(verdict.get("auto")))
            if verdict["verdict"] == "accept":
                t["note"] = verdict.get("note", "")
                t["endedAt"] = now_ms()
                self._set_status(t, "accepted")
                flow["done"] = sum(1 for x in flow["tasks"] if x["status"] == "accepted")
                if flow["running"]:
                    self.set_phase("delegate")
                self.emit()
                self._write_docs()
                await self._commit(t)
                await self._module_gate(t)
                return
            t["defects"] = verdict.get("defects") or ["The review rejected the previous attempt."]
            if t["round"] >= max_rounds:
                if cfg["flow"].get("escalateOnFail", True) and not t.get("escalated") and planner.TIERS.index(t["tier"]) < planner.TIERS.index("heavy"):
                    t["escalated"] = True
                    t["tier"] = planner.TIERS[min(planner.TIERS.index("heavy"), planner.TIERS.index(t["tier"]) + 1)]
                    t["round"] += 1
                    self.notice(f"Task {t['n']} escalated to {t['tier']} for a last attempt.")
                    continue
                t["note"] = f"Returned {t['round'] + 1} times; still: " + "; ".join(t["defects"])[:300]
                t["endedAt"] = now_ms()
                self._set_status(t, "failed")
                return
            t["round"] += 1
            new_tier = planner.norm_tier(verdict.get("tier", ""), t["tier"])
            if planner.TIERS.index(new_tier) > planner.TIERS.index(t["tier"]):
                t["tier"] = new_tier  # return to the same or a higher tier, never lower
            self.emit()

    def _notes_digest(self) -> str:
        notes = self.flow.get("notes") or []
        if not notes:
            return ""
        lines = [f"- [{n['author']}] {n['text']}" for n in notes[-15:]]
        return "\n\n## Shared notes from other workers (interfaces, conventions)\n" + "\n".join(lines)

    def _review_card(self, t: dict, run: dict, changes: dict, verdict: dict) -> None:
        msg = self.sink.add_message("assistant", "savvy-orchestrator")
        self.sink.add_part(msg, {
            "type": "review", "taskN": t["n"], "title": t["title"], "tier": t["tier"], "round": t["round"],
            "runId": run.get("id", ""), "status": "done", "verdict": verdict["verdict"], "defects": verdict.get("defects", []),
            "note": verdict.get("note", ""), "files": list(changes.keys()), "auto": bool(verdict.get("auto")),
            "checks": t.get("checks", []),
        })

    async def review(self, t: dict, report: str, changes: dict, run: dict, checks: list[dict]) -> dict:
        async with self.review_lock:
            self.set_phase("review")
            cfg = self.cfg()
            card_msg = self.sink.add_message("assistant", "savvy-orchestrator")
            card = self.sink.add_part(card_msg, {
                "type": "review", "taskN": t["n"], "title": t["title"], "tier": t["tier"], "round": t["round"],
                "runId": run.get("id", ""), "status": "reviewing", "verdict": None, "defects": [], "note": "",
                "files": list(changes.keys()), "checks": t.get("checks", []),
            })
            if run.get("status") != "done":
                verdict = {"verdict": "return", "defects": [run.get("report") or "The worker failed."], "tier": t["tier"],
                           "note": "The worker failed or timed out."}
            else:
                check_text = "\n".join(f"- `{c['command']}` -> exit {c['exitCode']}\n{c['output']}" for c in checks) or "(no automated check)"
                content = (
                    f"TASK {t['n']}: {t['title']}  (tier {t['tier']}, round {t['round']}, module {t.get('module') or '-'})\n\n"
                    f"BRIEF:\n{t['brief']}\n\nFILES IT OWNS: {', '.join(t.get('files') or []) or '-'}\n"
                    f"VERIFY: {t['verify'] or '(not specified)'}\n\nAUTOMATED VERIFICATION:\n{check_text}\n\n"
                    f"WORKER REPORT:\n{report}\n\nDIFF OF THE FILES IT CHANGED:\n{changes_diff(self.session.directory, changes)}"
                )
                spec = self.orch_spec(prompts.ORCH_REVIEW, READ_ONLY + ["bash", "edit"], int(cfg["flow"].get("reviewSteps") or 8))
                history = [{"role": "user", "content": content}]
                try:
                    out = await run_agent(self.session, spec, history, self.sink, make_context(self.session, spec.name))
                    text = out.text
                except llm.LLMError as e:
                    text = ""
                    journal.log(self.session, "error", error=f"review failed: {e}")
                verdict = extract_json(text) or (await self._json_repair(text, '{"verdict": "accept|return", "defects": [], "tier": "", "note": ""}') if text else None) or {}
                if verdict:
                    self._hide_last_text(text)
                v = str(verdict.get("verdict", "")).lower()
                if v not in ("accept", "return", "escalate"):
                    low = text.lower()
                    failing = checks and checks[-1]["exitCode"] != 0
                    v = "return" if failing or ("return" in low and "accept" not in low) else "accept"
                    verdict.setdefault("note", "Verdict inferred from an unstructured review.")
                verdict["verdict"] = "accept" if v == "escalate" else v
                verdict["defects"] = [str(d) for d in (verdict.get("defects") or []) if str(d).strip()]
            card.update(status="done", verdict=verdict["verdict"], defects=verdict.get("defects", []),
                        note=str(verdict.get("note") or ""))
            self.sink.update_part(card_msg, card)
            return verdict

    async def _module_gate(self, t: dict) -> None:
        """When a module's last task is accepted, run its check; a failure becomes a fix task."""
        module = self._module(t.get("module", ""))
        if not module or not module.get("verifyCommand") or module.get("gateDone"):
            return
        mine = [x for x in self.flow["tasks"] if x.get("module") == module["id"]]
        if not all(x["status"] == "accepted" for x in mine):
            return
        module["gateDone"] = True
        spec = self.orch_spec("", ["bash"], 1)
        res = await run_check(self.session, self.sink, spec, make_context(self.session, spec.name),
                              module["verifyCommand"], int(self.cfg()["verify"].get("timeout") or 300))
        module["gate"] = res["exitCode"]
        if res["exitCode"] != 0:
            self._add_fix_tasks([{
                "title": f"Fix module {module['id']} check", "tier": "careful", "size": "M", "complexity": 3,
                "files": module["files"], "brief": f"The module check `{module['verifyCommand']}` fails after all tasks of module "
                f"{module['name']} were accepted. Find and fix the cause.\n\nOutput:\n{res['output']}",
                "verify": "the module check passes", "verifyCommand": module["verifyCommand"], "module": module["id"],
            }], reason=f"module {module['id']} check failed")

    def _add_fix_tasks(self, raw: list[dict], reason: str) -> None:
        flow = self.flow
        base = len(flow["tasks"])
        new = []
        for i, r in enumerate(raw):
            c = planner.clean_task(r, i)
            if not c:
                continue
            c.update(n=base + len(new) + 1, after=[], status="planned", round=0, runIds=[], defects=[], note="",
                     module=r.get("module", c.get("module", "")), fix=True, merged=1)
            new.append(c)
        if not new:
            return
        flow["tasks"].extend(new)
        planner.serialize_conflicts(flow["tasks"])
        flow["stats"] = {**flow.get("stats", {}), **planner.schedule(flow["tasks"])}
        flow["total"] = len(flow["tasks"])
        journal.log(self.session, "fix", reason=reason, tasks=[t["title"] for t in new])
        self.notice(f"{len(new)} fix task(s) added: {reason}.")
        self.emit()

    async def _integration(self) -> bool:
        """Project-wide verification. Returns True when fix tasks were added (run the DAG again)."""
        cfg = self.cfg()
        flow = self.flow
        if any(t["status"] in ("planned", "running", "review") for t in flow["tasks"]):
            return False
        commands = list(dict.fromkeys(flow.get("verifyCommands", []) + cfg["verify"].get("commands", [])))
        if not commands or not cfg["flow"].get("autoVerify", True):
            return False
        self.set_phase("verify")
        spec = self.orch_spec(prompts.orch_fix(), READ_ONLY + ["bash"], 6)
        ctx = make_context(self.session, spec.name)
        results = [await run_check(self.session, self.sink, spec, ctx, c, int(cfg["verify"].get("timeout") or 300)) for c in commands]
        flow["integration"] = [{k: v for k, v in r.items() if k != "output"} for r in results]
        self.emit()
        failing = [r for r in results if r["exitCode"] != 0]
        if not failing or flow.get("integrationRound", 0) >= int(cfg["flow"].get("integrationRounds", 2)):
            return False
        flow["integrationRound"] = flow.get("integrationRound", 0) + 1
        content = "Failing project-wide checks:\n" + "\n\n".join(f"`{r['command']}` exit {r['exitCode']}:\n{r['output']}" for r in failing)
        content += "\n\nAccepted tasks:\n" + "\n".join(f"- {t['n']}. {t['title']} ({', '.join(t.get('files') or [])})" for t in flow["tasks"] if t["status"] == "accepted")
        history = [{"role": "user", "content": content}]
        try:
            out = await run_agent(self.session, spec, history, self.sink, ctx)
        except llm.LLMError as e:
            journal.log(self.session, "error", error=f"fix planning failed: {e}")
            return False
        data = extract_json(out.text) or await self._json_repair(out.text, '{"tasks": [...]}') or {}
        if data:
            self._hide_last_text(out.text)
        raw = data.get("tasks") or []
        for r in raw:
            r.setdefault("verifyCommand", failing[0]["command"])
        before = len(flow["tasks"])
        self._add_fix_tasks(raw, f"integration round {flow['integrationRound']}")
        return len(flow["tasks"]) > before

    async def close(self) -> None:
        flow = self.flow
        self.set_phase("close")
        lines = [f"Request: {flow['request'][:3000]}", f"Plan: {flow['title']} - {flow['summary']}", ""]
        runs = {r["id"]: r for r in self.session.data.get("runs", [])}
        tiers_used: set[str] = set()
        for t in flow["tasks"]:
            lines.append(f"Task {t['n']} [{t['status']}] {t['title']} (module {t.get('module') or '-'}, tier {t['tier']}, rounds {t['round'] + 1})")
            if t.get("note"):
                lines.append(f"  review note: {t['note']}")
            for c in t.get("checks", [])[-1:]:
                lines.append(f"  check `{c['command']}` -> exit {c['exitCode']}")
            for rid in t["runIds"][-1:]:
                r = runs.get(rid)
                if r:
                    tiers_used.add(r["type"])
                    lines.append("  last worker report: " + r["report"][:800].replace("\n", "\n  "))
        for r in flow.get("integration", []):
            lines.append(f"Integration check `{r['command']}` -> exit {r['exitCode']}")
        if flow.get("budgetExceeded"):
            lines.append("Stopped early: token budget reached.")
        lines.append("")
        lines.append("Tiers that ran: " + (", ".join(sorted(tiers_used)) or "none"))
        spec = self.orch_spec(prompts.ORCH_CLOSE, [], 1)
        history = [{"role": "user", "content": _tail("\n".join(lines), 24000)}]
        try:
            await run_agent(self.session, spec, history, self.sink, make_context(self.session, spec.name))
        except llm.LLMError as e:
            self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "error", "text": f"Close summary failed: {e}"}])
        flow.update(isFinished=True, endedAt=now_ms(), running=0, executing=False)
        journal.log(self.session, "flow_end", done=flow["done"], total=flow["total"], tokens=flow.get("tokens", 0))
        self.emit()
        await self._auto_pull_request()
        if self.cfg()["flow"].get("writeReport", True):
            try:
                data = {**self.session.data, "_journal": journal.read(self.session.id, 400,
                        {"phase", "task", "verdict", "verify", "commit", "plan", "fix", "split", "error", "compaction", "loop"})}
                docs = Path(self.session.directory) / "docs"
                docs.mkdir(parents=True, exist_ok=True)
                write_text(docs / "FLOW_REPORT.md", journal.report(data))
            except OSError:
                pass

    # -- side effects -----------------------------------------------------
    async def _auto_pull_request(self) -> None:
        """Bitbucket PR when the flow ends (Settings > Bitbucket > open a PR after each flow)."""
        cfg = self.cfg()["bitbucket"]
        if not cfg.get("autoPrOnFlow") or not cfg.get("token") or not self.flow["done"]:
            return
        from . import bitbucket

        try:
            st = await bitbucket.status(self.session.directory, cfg.get("remote") or "origin")
            _, stat = await bitbucket.git(self.session.directory, "diff", "--stat", "HEAD") if st.get("hasCommits") else (0, "")
            draft = bitbucket.draft_description({**self.session.data}, stat)
            pr = await bitbucket.create_pull_request(self.session.directory, cfg, title=draft["title"],
                                                     description=draft["description"])
        except Exception as e:  # noqa: BLE001 - reported, never breaks the flow
            self.sink.add_message("assistant", "savvy-orchestrator", parts=[{"type": "error", "text": f"Automatic pull request failed: {e}"}])
            journal.log(self.session, "error", error=f"auto PR: {e}")
            return
        entry = {**pr, "title": draft["title"], "createdAt": now_ms()}
        self.session.data.setdefault("pullRequests", []).append(entry)
        self.flow["pullRequest"] = pr["url"]
        journal.log(self.session, "pr", url=pr["url"], branch=pr["branch"], target=pr["target"], steps=pr["steps"], auto=True)
        self.session.emit("pr.created", {"pr": entry})
        self.notice(f"Pull request opened: {pr['url']}")
        self.emit()

    def _write_docs(self) -> None:
        flow = self.flow
        if not (flow.get("greenfield") or flow.get("mode") == "modular"):
            return
        root = Path(self.session.directory)
        docs = root / "docs"
        try:
            docs.mkdir(parents=True, exist_ok=True)
            if flow.get("spec"):
                spec_file = docs / "SPEC.md"
                if not spec_file.exists():
                    write_text(spec_file, flow["spec"].rstrip() + "\n")
            lines = [f"# PLAN: {flow['title']}", "", flow["summary"], ""]
            if flow.get("modules"):
                lines += ["## Modules", ""]
                for m in flow["modules"]:
                    lines.append(f"- **{m['id']}** {m['name']}: {m['goal']}" + (f" (after {', '.join(m['dependsOn'])})" if m["dependsOn"] else ""))
                    if m.get("contracts"):
                        lines.append("  Contracts: " + m["contracts"].replace("\n", " ")[:600])
                lines.append("")
            lines += ["## Tasks", ""]
            for t in flow["tasks"]:
                mark = "x" if t["status"] == "accepted" else ("!" if t["status"] in ("failed", "blocked") else " ")
                after = f" (after {', '.join(map(str, t['after']))})" if t["after"] else ""
                lines.append(f"- [{mark}] **{t['n']}. {t['title']}** - `{t['tier']}` {t.get('size', '')}/c{t.get('complexity', '')} "
                             f"wave {t.get('wave', '')} module `{t.get('module') or '-'}`{after}")
                lines.append(f"  {t['brief'].replace(chr(10), ' ')[:600]}")
                if t.get("verifyCommand"):
                    lines.append(f"  Verify: `{t['verifyCommand']}`")
            write_text(docs / "PLAN.md", "\n".join(lines) + "\n")
        except OSError:
            pass

    async def _commit(self, t: dict) -> None:
        if not self.cfg()["flow"].get("gitCommit"):
            return
        root = Path(self.session.directory)
        message = f"savvy-flow: task {t['n']} - {t['title']}\n\nFlow: {self.flow['id']}\nTier: {t['tier']}\nRound: {t['round'] + 1}"
        if not (root / ".git").exists():
            await run_command("git init -q", str(root), 60)
        await run_command("git add -A", str(root), 120)
        code, _out, _ = await run_command("git commit -q -F -", str(root), 120, stdin=message.encode("utf-8"))
        if code == 0:
            journal.log(self.session, "commit", n=t["n"], message=message.splitlines()[0])


def resume_engine(session: Session) -> FlowEngine:
    """Rebuild an engine for a flow whose engine is gone (server restart)."""
    engine = FlowEngine(session)
    flow = session.data["flow"]
    engine.plan_history = [{"role": "user", "content": f"Task from the user:\n{flow['request']}"}]
    engine._plan_system = prompts.orch_plan(bool(flow.get("greenfield")))
    return engine
