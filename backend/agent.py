"""The agent loop shared by every role: build, plan, savvy orchestrator, savvy workers.

One turn: send history to the LLM, stream text into the transcript, run the tool
calls it asked for (asking the user when a permission says so), feed results back,
repeat until the model answers without tools or the step budget is spent.

Long-run hardening (OpenCode / OpenClaude):
- transient LLM errors are retried with exponential backoff and jitter;
- old tool outputs are pruned and old history is summarized into a checkpoint
  before the context fills up; a context-overflow error triggers a compaction;
- identical tool calls repeated in a row are blocked (doom-loop guard);
- long tool outputs are cut and spilled to a file;
- every tool call is written to the session journal.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import compaction, extensions, journal, llm, mcp_tools, mods, permissions
from .config import auto_mode, load_config, permission_mode
from .session import MessageSink, Session, new_id, now_ms
from .tools import TOOLS, ToolContext, ToolDef, ToolError, _clip, bound_output

UI_OUTPUT_LIMIT = 12_000

# Models that refused native tools: (provider, baseUrl, model) -> use the text protocol.
_text_protocol: set[tuple[str, str, str]] = set()


@dataclass
class AgentSpec:
    name: str
    system: str
    tools: list[str]
    model: str
    max_steps: int = 50
    temperature: Optional[float] = None
    # Per-category overrides of the configured permissions ("allow" | "ask" | "deny").
    permissions: dict = field(default_factory=dict)
    # Leading history messages never summarized away (the request / the brief).
    pinned: int = 1
    # Called with every ChatResult (token / context accounting for runs).
    on_usage: Optional[Callable[[llm.ChatResult], None]] = None
    # Called before each tool call (step counters for runs).
    on_tool: Optional[Callable[[llm.ToolCall], None]] = None
    # MCP tools, skills and mod tools/prompt sections: "all", "local" (no MCP), "read" (read-only agents) or "none".
    extensions: str = "none"
    # Tools specific to this agent (e.g. the mod builder's mod_validate / mod_install).
    extra_tools: dict = field(default_factory=dict)


@dataclass
class AgentOutcome:
    text: str
    steps: int
    input_tokens: int
    output_tokens: int
    stopped: str = ""  # "" | "loop" | "budget"


def _tool_mode(cfg: dict, model: str) -> str:
    if cfg.get("provider") == "n8n" or cfg.get("toolMode") == "prompt":
        return "prompt"
    if cfg.get("toolMode") == "native":
        return "native"
    key = (cfg.get("provider", ""), cfg.get("baseUrl", ""), model)
    return "prompt" if key in _text_protocol else "native"


# ---------------------------------------------------------------------------
# permissions and questions


async def _wait_user(session: Session, registry: str, entry: dict) -> str:
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    store = session.permissions if registry == "permission" else session.questions
    store[entry["id"]] = fut
    session.emit(f"{registry}.asked", {registry: entry})
    try:
        return await fut
    except asyncio.CancelledError:
        entry["status"] = "rejected"
        session.emit(f"{registry}.resolved", {registry: entry})
        raise
    finally:
        store.pop(entry["id"], None)


async def ask_permission(session: Session, sink: MessageSink, msg: dict, part: dict, category: str, agent: str,
                         grant: Optional[str] = None, note: str = "", forced: bool = False) -> str:
    entry = {
        "id": new_id("perm"), "category": category, "tool": part["tool"], "input": part["input"], "agent": agent,
        "runId": sink.run_id, "messageId": msg["id"], "partId": part["id"], "status": "pending", "createdAt": now_ms(),
        "note": note, "forced": forced,
    }
    session.data.setdefault("permissionLog", []).append(entry)
    part["permissionId"] = entry["id"]
    response = await _wait_user(session, "permission", entry)
    entry["status"] = {"once": "allowed", "always": "allowed", "reject": "rejected"}.get(response, "rejected")
    if response == "always":
        grants = session.data.setdefault("alwaysAllow", [])
        grant = grant or (permissions.grant_pattern(part["input"].get("command", "")) if category == "bash" else category)
        if grant not in grants:
            grants.append(grant)
    session.emit("permission.resolved", {"permission": entry})
    journal.log(session, "permission", tool=part["tool"], category=category, agent=agent, status=entry["status"],
                target=str(part["input"].get("command") or part["input"].get("path") or "")[:200])
    session.touch()
    return response


def settle_pending(session: Session, mode: str) -> int:
    """The user switched the session to a more permissive mode: prompts that mode no longer asks are approved
    (Claude Code behaviour). Prompts every mode asks (network commands, agent-level "ask") stay."""
    settled = 0
    for entry in session.data.get("permissionLog", []):
        fut = session.permissions.get(entry.get("id"))
        if entry.get("status") != "pending" or not fut or fut.done() or entry.get("forced") or entry.get("note"):
            continue
        if mode == "auto" or (mode == "acceptEdits" and entry.get("category") == "edit"):
            fut.set_result("once")
            settled += 1
    return settled


async def ask_questions(session: Session, agent: str, run_id: Optional[str], questions: list[dict]) -> list[list[str]]:
    entry = {"id": new_id("q"), "agent": agent, "runId": run_id, "questions": questions, "status": "pending",
             "createdAt": now_ms(), "answers": None}
    session.data.setdefault("questionLog", []).append(entry)
    answers = await _wait_user(session, "question", entry)
    answers = [[str(a) for a in (ans if isinstance(ans, list) else [ans])] for ans in (answers or [])]
    entry.update(status="answered", answers=answers)
    session.emit("question.resolved", {"question": entry})
    journal.log(session, "question", agent=agent, questions=[q["question"] for q in questions], answers=answers)
    session.touch()
    return answers


EDIT_PATH_KEYS = ("path", "file_path", "filePath")


def _edit_paths(session: Session, args: dict) -> list[str]:
    """Workspace-relative paths an edit tool call touches (apply_patch: every file of the patch)."""
    raw = [str(args.get(k)) for k in EDIT_PATH_KEYS if args.get(k)]
    patch = str(args.get("patch") or args.get("patchText") or "")
    raw += [a or b for a, b in re.findall(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$|^\*\*\* Move to:\s*(.+)$", patch, re.M)]
    root = Path(session.directory).resolve()
    out = []
    for r in raw:
        p = Path(r.strip())
        p = (p if p.is_absolute() else root / p).resolve()
        try:
            out.append(p.relative_to(root).as_posix())
        except ValueError:
            out.append(p.as_posix())
    return out


def _edit_granted(session: Session, args: dict, grants: list) -> bool:
    """Approving a savvy-flow plan grants edits to the files its tasks own ("edit:<glob>")."""
    patterns = [g[5:] for g in grants if isinstance(g, str) and g.startswith("edit:")]
    paths = _edit_paths(session, args)
    return bool(patterns and paths) and all(any(fnmatch.fnmatch(p, pat) for pat in patterns) for p in paths)


def decide(session: Session, spec: AgentSpec, category: Optional[str], args: dict,
           tool: Optional[ToolDef] = None) -> tuple[str, str]:
    """(allow | ask | deny, reason) for one tool call.

    Permission modes (Claude Code style): ``ask`` confirms edits, commands, app launches and writes;
    ``acceptEdits`` applies file edits without asking; ``auto`` asks nothing. Deny rules, the plan
    agent's read-only restriction and local-only network rules apply in every mode."""
    if not category:
        return "allow", ""
    full = load_config()
    cfg = full["permissions"]
    mode = permission_mode(full, session.data)
    grants = session.data.get("alwaysAllow", [])
    override = spec.permissions.get(category)
    if override == "deny":
        return "deny", f"{category} actions are not allowed for the {spec.name} agent (read-only)"
    reason = ""
    if category == "mcp" and tool is not None:
        servers = mcp_tools.session_servers(full, session)
        value = mcp_tools.decision(full, servers, mcp_tools.tool_server(tool, args), mcp_tools.tool_read_only(tool, args), grants)
        if override == "ask" and value != "deny":
            value = "ask"
        if value == "deny":
            reason = "the MCP server or tool is denied in Settings > MCP"
    elif category == "bash":
        command = str(args.get("command") or "")
        value = permissions.bash_decision(command, cfg.get("bashRules", []), override or cfg.get("bash", "ask"), grants)
        if value == "deny":
            return "deny", "a shell deny rule matches this command (Settings > Workspace)"
        net = full.get("network") or {}
        kind = permissions.network_kind(command) if net.get("localOnly", True) else None
        if kind == "network" or (kind == "package" and not net.get("allowPackageManagers", True)):
            if mode == "auto":
                return "deny", ("local-only mode: commands that reach the network are never run unattended. Ask the user to run "
                                "it in the terminal, or to turn local-only mode off in Settings > Privacy & security")
            return "ask", ("this command reaches the network (local-only mode is on)" if kind == "network"
                           else "this command installs packages from the network")
    elif category == "edit":
        if cfg.get("edit") == "deny":
            return "deny", "file edits are denied in Settings > Workspace"
        # An explicit "allow" in Settings acts like a Claude Code allow rule: edits never ask.
        granted = "edit" in grants or _edit_granted(session, args, grants) or (cfg.get("edit") == "allow" and not override)
        value = "allow" if (mode in ("acceptEdits", "auto") or granted) else "ask"
        if override == "ask" and not granted:
            value = "ask"
    else:
        value = override or cfg.get(category, "ask")
        if value == "deny":
            return "deny", f"{category} is denied in Settings"
        if value == "ask" and category in grants:
            value = "allow"
    if value == "ask" and mode == "auto" and override != "ask":
        value = "allow"
    return value, reason


def resolve_permission(session: Session, spec: AgentSpec, category: Optional[str], args: dict,
                       tool: Optional[ToolDef] = None) -> str:
    return decide(session, spec, category, args, tool)[0]


def _grant_for(tool: ToolDef, args: dict) -> Optional[str]:
    if tool.permission == "mcp":
        server = mcp_tools.tool_server(tool, args)
        return f"mcp:{server}" if server else "mcp"
    return None


AUTO_ANSWER = ("No user is available (auto mode). Choose the most reasonable option yourself, "
               "state the assumption in one line, and continue.")


async def auto_answer(session: Session, agent: str, questions: list[dict]) -> list[list[str]]:
    journal.log(session, "question", agent=agent, questions=[q["question"] for q in questions], auto=True)
    return [[AUTO_ANSWER] for _ in questions]


# ---------------------------------------------------------------------------
# tools


async def execute_tool(session: Session, sink: MessageSink, msg: dict, spec: AgentSpec, ctx: ToolContext,
                       call: llm.ToolCall, output_limit: int, registry: Optional[dict[str, ToolDef]] = None) -> tuple[str, bool]:
    """Returns (text for the model, ok). Mods' ``tool.call`` hooks wrap permission + execution."""
    if registry is None:
        registry = {n: TOOLS[n] for n in spec.tools if n in TOOLS}
    part = sink.add_part(msg, {
        "type": "tool", "tool": call.name, "callId": call.id, "input": call.arguments,
        "status": "running", "title": "", "output": "", "metadata": {}, "startedAt": now_ms(),
    })
    started = time.monotonic()
    args = call.arguments
    target = str(args.get("path") or args.get("command") or args.get("pattern") or args.get("url")
                 or args.get("description") or args.get("tool") or args.get("name") or "")[:200]

    def finish(status: str, output: str, title: str = "", metadata: Optional[dict] = None) -> None:
        meta = metadata or {}
        part.update(status=status, output=_clip(output, UI_OUTPUT_LIMIT), title=title or part["title"],
                    metadata=meta, endedAt=now_ms())
        sink.update_part(msg, part)
        journal.log(session, "tool", agent=spec.name, runId=sink.run_id, tool=call.name, target=target, status=status,
                    ms=int((time.monotonic() - started) * 1000),
                    **({"exitCode": meta["exitCode"]} if "exitCode" in meta else {}),
                    **({"mcp": meta["mcp"]} if "mcp" in meta else {}),
                    **({"skill": meta["skill"]} if "skill" in meta else {}),
                    **({"error": output[:300]} if status == "error" else {}))

    async def engine(ev: dict) -> dict:
        a = ev.get("input") if isinstance(ev.get("input"), dict) else args
        if a is not args:  # rewritten by a mod
            part["input"] = a
            sink.update_part(msg, part)
        tool = registry.get(call.name)
        if not tool:
            return {"ok": False, "output": f"Unknown or unavailable tool '{call.name}'. Available tools: {', '.join(registry)}"}
        category = tool.dynamic_permission(a) if tool.dynamic_permission else tool.permission
        decision, reason = decide(session, spec, category, a, tool)
        if decision == "deny":
            return {"ok": False, "output": (f"Permission denied: this {category} action is not allowed for the {spec.name} agent"
                                            + (f" ({reason})" if reason else " (deny rule or read-only mode)") + ". Use another approach.")}
        if decision == "ask":
            part["status"] = "waiting"
            sink.update_part(msg, part)
            answer = await ask_permission(session, sink, msg, part, category, spec.name, _grant_for(tool, a), reason,
                                         spec.permissions.get(category) == "ask")
            if answer == "reject":
                return {"ok": False, "display": "Rejected by user",
                        "output": "The user rejected this tool call. Do not retry it; adapt your approach or ask the user."}
            part["status"] = "running"
            sink.update_part(msg, part)
        try:
            result = await tool.run(ctx, a)
        except asyncio.CancelledError:
            raise
        except ToolError as e:
            return {"ok": False, "output": f"Error: {e}", "display": str(e)}
        except Exception as e:  # noqa: BLE001 - reported to the model, which can recover
            return {"ok": False, "output": f"Error: {type(e).__name__}: {e}", "display": f"{type(e).__name__}: {e}"}
        return {"ok": True, "output": result.output, "title": result.title, "metadata": dict(result.metadata)}

    event = {"tool": call.name, "input": args, "agent": spec.name, "runId": sink.run_id}
    try:
        res = await mods.dispatch("tool.call", session, event, engine, tool_ctx=ctx)
    except asyncio.CancelledError:
        finish("error", "Aborted")
        raise
    if not isinstance(res, dict):
        res = {"ok": True, "output": str(res)}
    if "deny" in res:
        text = f"Blocked by the {res.get('mod') or 'a'} mod: {res['deny']}"
        finish("error", text, metadata={"blockedBy": res.get("mod") or "mod"})
        return text + " Do not retry the same action; adapt your approach.", False
    output = str(res.get("output", ""))
    if not res.get("ok", True):
        finish("error", str(res.get("display") or output))
        return output, False
    model_text, spill = bound_output(output, output_limit)
    meta = dict(res.get("metadata") or {})
    finish("done", output, str(res.get("title") or ""), {**meta, **({"spill": spill} if spill else {})})
    return model_text, True


# ---------------------------------------------------------------------------
# the loop


async def _call_llm(session: Session, sink: MessageSink, spec: AgentSpec, cfg: dict, agent_cfg: dict,
                    messages: list[dict], schemas: list[dict], mode: str, json_mode: bool):
    """One LLM call streamed into a fresh message, retried on transient errors."""
    attempt = 0
    while True:
        msg = sink.add_message("assistant", spec.name, spec.model)
        parts: dict[str, dict] = {}

        async def on_delta(kind: str, chunk: str) -> None:
            if kind not in parts:
                parts[kind] = sink.add_part(msg, {"type": kind, "text": "", **({"startedAt": now_ms()} if kind == "reasoning" else {})})
            if kind == "text" and "reasoning" in parts and not parts["reasoning"].get("endedAt"):
                parts["reasoning"]["endedAt"] = now_ms()
                sink.update_part(msg, parts["reasoning"])
            sink.delta(msg, parts[kind], chunk)

        try:
            res = await llm.chat(
                cfg, model=spec.model, messages=messages,
                tools=schemas if (mode == "native" and schemas) else None,
                on_delta=on_delta, json_mode=json_mode, temperature=spec.temperature,
            )
            return msg, parts, res
        except llm.LLMError as e:
            for p in parts.values():  # drop the partial stream
                p["hidden"] = True
                sink.update_part(msg, p)
            if isinstance(e, (llm.ToolsUnsupported, llm.ContextOverflow)) or not e.transient:
                raise
            attempt += 1
            max_retries = int(agent_cfg.get("maxRetries", 4))
            if attempt > max_retries:
                raise
            base = float(agent_cfg.get("retryBaseSeconds", 2.0))
            delay = min(60.0, base * 2.0 ** (attempt - 1)) + random.uniform(0, base * 0.75)
            sink.add_part(msg, {"type": "notice", "text": f"LLM error, retry {attempt}/{max_retries} in {delay:.0f}s: {str(e)[:160]}"})
            journal.log(session, "retry", agent=spec.name, runId=sink.run_id, attempt=attempt, error=str(e)[:300])
            await asyncio.sleep(delay)


async def _manage_context(session: Session, sink: MessageSink, spec: AgentSpec, cfg: dict, ctx_cfg: dict,
                          history: list[dict], window: int, force: bool = False) -> None:
    cleared = compaction.prune(history, window, {**ctx_cfg, **({"pruneAt": 0} if force else {})})
    if cleared:
        journal.log(session, "prune", agent=spec.name, runId=sink.run_id, cleared=cleared)
    if not (force or compaction.needs_summary(history, window, ctx_cfg)):
        return
    keep_cfg = {**ctx_cfg, "keepRecentShare": float(ctx_cfg.get("keepRecentShare", 0.3)) * (0.5 if force else 1)}
    try:
        stats = await compaction.summarize(cfg, spec.model, history, window, keep_cfg, spec.pinned)
    except llm.LLMError as e:
        journal.log(session, "error", agent=spec.name, error=f"compaction failed: {e}")
        return
    if stats:
        msg = sink.add_message("assistant", spec.name, spec.model)
        sink.add_part(msg, {"type": "compaction", "text": stats["summary"], "before": stats["before"], "after": stats["after"]})
        journal.log(session, "compaction", agent=spec.name, runId=sink.run_id, before=stats["before"], after=stats["after"])
        session.data["compactions"] = session.data.get("compactions", 0) + 1
        if spec.on_usage:
            spec.on_usage(llm.ChatResult("", prompt_tokens=stats["tokens"], completion_tokens=0))


async def run_agent(
    session: Session,
    spec: AgentSpec,
    history: list[dict],
    sink: MessageSink,
    ctx: ToolContext,
    *,
    json_mode: bool = False,
) -> AgentOutcome:
    """Run until the model answers without tools. ``history`` is extended in place."""
    full_cfg = load_config()
    cfg, ctx_cfg, agent_cfg = full_cfg["llm"], full_cfg["context"], full_cfg["agent"]
    window = int(cfg.get("contextWindow") or 16384)
    output_limit = int(ctx_cfg.get("toolOutputMaxChars") or 12000)
    loop_threshold = max(2, int(agent_cfg.get("doomLoopThreshold") or 3))
    ctx.require_read = bool(agent_cfg.get("requireReadBeforeEdit", True))
    if "question" in spec.tools and ctx.ask is None:
        if auto_mode(full_cfg, session.data):
            ctx.ask = lambda qs: auto_answer(session, spec.name, qs)
        else:
            ctx.ask = lambda qs: ask_questions(session, spec.name, sink.run_id, qs)

    def notify(text: str) -> None:
        note = sink.add_message("assistant", spec.name, spec.model)
        sink.add_part(note, {"type": "notice", "text": text})

    extras = await extensions.prepare(session, spec, ctx, notify)
    registry: dict[str, ToolDef] = {**{n: TOOLS[n] for n in spec.tools if n in TOOLS}, **extras.tools, **spec.extra_tools}

    steps = in_tok = out_tok = 0
    final_text = ""
    exhausted = overflowed = nudged = False
    recent: list[str] = []
    loop_hits = failures_in_row = 0
    stopped = ""

    while True:
        await _manage_context(session, sink, spec, cfg, ctx_cfg, history, window)
        mode = _tool_mode(cfg, spec.model)
        tool_defs = list(registry.values()) if not exhausted else []
        schemas = [t.schema() for t in tool_defs]
        system = spec.system + extras.system
        convo = history
        if mode == "prompt":
            if schemas:
                system = f"{system}\n\n{llm.tool_protocol_prompt(schemas)}"
            convo = llm.flatten_for_text_protocol(history)
        messages = [{"role": "system", "content": system}] + convo

        try:
            msg, parts, res = await _call_llm(session, sink, spec, cfg, agent_cfg, messages, schemas, mode, json_mode)
        except llm.ToolsUnsupported:
            _text_protocol.add((cfg.get("provider", ""), cfg.get("baseUrl", ""), spec.model))
            notice = sink.add_message("assistant", spec.name, spec.model)
            sink.add_part(notice, {"type": "notice", "text": f"{spec.model}: native tool calling unavailable, switching to the text tool protocol."})
            continue
        except llm.ContextOverflow:
            if overflowed:
                raise
            overflowed = True
            await _manage_context(session, sink, spec, cfg, ctx_cfg, history, window, force=True)
            continue
        overflowed = False

        known = {t.name for t in tool_defs}
        text, calls = res.text, res.tool_calls
        if known and (mode == "prompt" or not calls):
            text, parsed = llm.parse_text_tool_calls(text, known)
            calls = calls or parsed
        text = text.strip()

        if "text" in parts:
            parts["text"]["text"] = text
            sink.update_part(msg, parts["text"])
            if not text:
                msg["parts"].remove(parts["text"])
        elif text:
            sink.add_part(msg, {"type": "text", "text": text})
        if "reasoning" in parts:
            parts["reasoning"].setdefault("endedAt", now_ms())
            sink.update_part(msg, parts["reasoning"])
        elif res.reasoning and res.reasoning.strip():
            # Some servers return the reasoning in one piece at the end (no stream).
            sink.add_part(msg, {"type": "reasoning", "text": res.reasoning, "startedAt": msg.get("createdAt") or now_ms(),
                                "endedAt": now_ms()})

        in_tok += res.prompt_tokens
        out_tok += res.completion_tokens
        msg["tokens"] = {"input": res.prompt_tokens, "output": res.completion_tokens}
        tot = session.data.setdefault("tokens", {"input": 0, "output": 0})
        tot["input"] += res.prompt_tokens
        tot["output"] += res.completion_tokens
        if sink.run_id is None:
            session.data["contextTokens"] = res.prompt_tokens + res.completion_tokens
        sink.update_message(msg)
        if spec.on_usage:
            spec.on_usage(res)

        history.append({
            "role": "assistant",
            "content": text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
        })
        if not calls:
            history[-1].pop("tool_calls")
            final_text = text
            if not text and not msg["parts"]:
                sink.add_part(msg, {"type": "notice", "text": "The model returned an empty answer."})
            break

        for call in calls:
            steps += 1
            if spec.on_tool:
                spec.on_tool(call)
            signature = call.name + json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
            recent.append(signature)
            if len(recent) >= loop_threshold and len(set(recent[-loop_threshold:])) == 1:
                loop_hits += 1
                journal.log(session, "loop", agent=spec.name, runId=sink.run_id, tool=call.name)
                sink.add_part(msg, {"type": "notice", "text": f"Loop guard: identical {call.name} call blocked."})
                output, ok = (f"Doom-loop guard: you called {call.name} with identical arguments {loop_threshold} times "
                              "in a row. It will not run again. Change your approach, or finish with what you have."), False
            else:
                output, ok = await execute_tool(session, sink, msg, spec, ctx, call, output_limit, registry)
            failures_in_row = 0 if ok else failures_in_row + 1
            history.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": output})

        if loop_hits >= 2:
            stopped = "loop"
            note = sink.add_message("assistant", spec.name, spec.model)
            sink.add_part(note, {"type": "error", "text": f"{spec.name} stopped: it kept repeating the same tool call."})
            final_text = "Stopped by the loop guard after repeating the same tool call. Work may be incomplete."
            break
        if failures_in_row >= 6 and not nudged:
            nudged = True
            history.append({"role": "user", "content": "Several tool calls failed in a row. Stop and think: re-read the "
                            "relevant files, check paths and exact text, then try a different approach."})
        if steps >= spec.max_steps and not exhausted:
            exhausted = True
            stopped = "budget"
            history.append({"role": "user", "content": "You have used all your tool calls. Do not call tools any more: "
                            "write your final answer / report now (what is done, what remains, whether another run is needed)."})

    return AgentOutcome(final_text, steps, in_tok, out_tok, stopped)


def make_context(session: Session, agent: str, run: Optional[dict] = None, directory: Optional[str] = None) -> ToolContext:
    def on_todos(todos: list) -> None:
        session.data["todos"] = todos
        session.emit("todos.updated", {"todos": todos})
        session.touch()

    cfg = load_config()
    return ToolContext(directory=Path(directory or session.directory), session=session, agent=agent, run=run, on_todos=on_todos,
                       repo_map_chars=int(cfg["context"].get("repoMapChars") or 6000))
