"""The agent loop shared by every role: build, plan, savvy orchestrator, savvy workers.

One turn: send history to the LLM, stream text into the transcript, run the tool
calls it asked for (asking the user when a permission says so), feed results back,
repeat until the model answers without tools or the step budget is spent.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import llm
from .config import load_config
from .session import MessageSink, Session, new_id, now_ms
from .tools import MAX_OUTPUT, TOOLS, ToolContext, ToolError, _clip

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
    # Called with every ChatResult (token / context accounting for runs).
    on_usage: Optional[Callable[[llm.ChatResult], None]] = None
    # Called before each tool call (step counters for runs).
    on_tool: Optional[Callable[[llm.ToolCall], None]] = None


@dataclass
class AgentOutcome:
    text: str
    steps: int
    input_tokens: int
    output_tokens: int


def _tool_mode(cfg: dict, model: str) -> str:
    if cfg.get("provider") == "n8n" or cfg.get("toolMode") == "prompt":
        return "prompt"
    if cfg.get("toolMode") == "native":
        return "native"
    key = (cfg.get("provider", ""), cfg.get("baseUrl", ""), model)
    return "prompt" if key in _text_protocol else "native"


def _prune(messages: list[dict], budget: int) -> list[dict]:
    """Drop the bodies of old tool results until the history fits the context."""
    if llm.messages_tokens(messages) <= budget:
        return messages
    out = [dict(m) for m in messages]
    for m in out[:-6]:
        if m["role"] == "tool" and len(m.get("content") or "") > 200:
            m["content"] = "[old tool output pruned to save context]"
            if llm.messages_tokens(out) <= budget:
                break
    return out


async def ask_permission(session: Session, sink: MessageSink, msg: dict, part: dict, category: str, agent: str) -> str:
    pid = new_id("perm")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    session.permissions[pid] = fut
    entry = {
        "id": pid,
        "category": category,
        "tool": part["tool"],
        "input": part["input"],
        "agent": agent,
        "runId": sink.run_id,
        "messageId": msg["id"],
        "partId": part["id"],
        "status": "pending",
        "createdAt": now_ms(),
    }
    session.data.setdefault("permissionLog", []).append(entry)
    part["permissionId"] = pid
    session.emit("permission.asked", {"permission": entry})
    try:
        response = await fut
    except asyncio.CancelledError:
        entry["status"] = "rejected"
        session.emit("permission.resolved", {"permission": entry})
        raise
    finally:
        session.permissions.pop(pid, None)
    entry["status"] = {"once": "allowed", "always": "allowed", "reject": "rejected"}.get(response, "rejected")
    if response == "always" and category not in session.data.setdefault("alwaysAllow", []):
        session.data["alwaysAllow"].append(category)
    session.emit("permission.resolved", {"permission": entry})
    session.touch()
    return response


def resolve_permission(session: Session, spec: AgentSpec, category: Optional[str]) -> str:
    if not category:
        return "allow"
    configured = load_config()["permissions"].get(category, "ask")
    value = spec.permissions.get(category, configured)
    if value == "ask" and category in session.data.get("alwaysAllow", []):
        return "allow"
    return value


async def execute_tool(session: Session, sink: MessageSink, msg: dict, spec: AgentSpec, ctx: ToolContext, call: llm.ToolCall) -> str:
    part = sink.add_part(msg, {
        "type": "tool", "tool": call.name, "callId": call.id, "input": call.arguments,
        "status": "running", "title": "", "output": "", "metadata": {}, "startedAt": now_ms(),
    })

    def finish(status: str, output: str, title: str = "", metadata: Optional[dict] = None) -> None:
        part.update(status=status, output=_clip(output, UI_OUTPUT_LIMIT), title=title or part["title"],
                    metadata=metadata or {}, endedAt=now_ms())
        sink.update_part(msg, part)

    tool = TOOLS.get(call.name)
    if not tool or call.name not in spec.tools:
        allowed = ", ".join(spec.tools)
        text = f"Unknown or unavailable tool '{call.name}'. Available tools: {allowed}"
        finish("error", text)
        return text

    decision = resolve_permission(session, spec, tool.permission)
    if decision == "deny":
        text = f"Permission denied: '{tool.permission}' is disabled for the {spec.name} agent."
        finish("error", text)
        return text
    if decision == "ask":
        part["status"] = "waiting"
        sink.update_part(msg, part)
        try:
            answer = await ask_permission(session, sink, msg, part, tool.permission, spec.name)
        except asyncio.CancelledError:
            finish("error", "Aborted")
            raise
        if answer == "reject":
            text = "The user rejected this tool call. Do not retry it; adapt your approach or ask the user."
            finish("error", "Rejected by user")
            return text
        part["status"] = "running"
        sink.update_part(msg, part)

    try:
        result = await tool.run(ctx, call.arguments)
    except asyncio.CancelledError:
        finish("error", "Aborted")
        raise
    except ToolError as e:
        finish("error", str(e))
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001 - reported to the model, which can recover
        finish("error", f"{type(e).__name__}: {e}")
        return f"Error: {type(e).__name__}: {e}"
    finish("done", result.output, result.title, result.metadata)
    return _clip(result.output, MAX_OUTPUT)


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
    cfg = load_config()["llm"]
    budget = int(int(cfg.get("contextWindow") or 32768) * 0.75)
    steps = in_tok = out_tok = 0
    final_text = ""
    exhausted = False

    while True:
        mode = _tool_mode(cfg, spec.model)
        tool_defs = [TOOLS[n] for n in spec.tools if n in TOOLS] if not exhausted else []
        schemas = [t.schema() for t in tool_defs]
        system = spec.system
        convo = history
        if mode == "prompt":
            if schemas:
                system = f"{system}\n\n{llm.tool_protocol_prompt(schemas)}"
            convo = llm.flatten_for_text_protocol(history)
        messages = [{"role": "system", "content": system}] + _prune(convo, budget)

        msg = sink.add_message("assistant", spec.name, spec.model)
        parts: dict[str, dict] = {}

        async def on_delta(kind: str, chunk: str) -> None:
            if kind not in parts:
                parts[kind] = sink.add_part(msg, {"type": kind, "text": ""})
            sink.delta(msg, parts[kind], chunk)

        try:
            res = await llm.chat(
                cfg, model=spec.model, messages=messages,
                tools=schemas if (mode == "native" and schemas) else None,
                on_delta=on_delta, json_mode=json_mode, temperature=spec.temperature,
            )
        except llm.ToolsUnsupported:
            _text_protocol.add((cfg.get("provider", ""), cfg.get("baseUrl", ""), spec.model))
            sink.add_part(msg, {"type": "notice", "text": f"{spec.model}: native tool calling unavailable, switching to the text tool protocol."})
            continue

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
            sink.update_part(msg, parts["reasoning"])

        in_tok += res.prompt_tokens
        out_tok += res.completion_tokens
        msg["tokens"] = {"input": res.prompt_tokens, "output": res.completion_tokens}
        tot = session.data.setdefault("tokens", {"input": 0, "output": 0})
        tot["input"] += res.prompt_tokens
        tot["output"] += res.completion_tokens
        sink.update_message(msg)
        if spec.on_usage:
            spec.on_usage(res)

        history.append({
            "role": "assistant",
            "content": text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
        })
        if not calls:
            if not history[-1]["tool_calls"]:
                history[-1].pop("tool_calls")
            final_text = text
            if not text and not msg["parts"]:
                sink.add_part(msg, {"type": "notice", "text": "The model returned an empty answer."})
            break

        for call in calls:
            steps += 1
            if spec.on_tool:
                spec.on_tool(call)
            output = await execute_tool(session, sink, msg, spec, ctx, call)
            history.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": output})

        if steps >= spec.max_steps and not exhausted:
            exhausted = True
            history.append({"role": "user", "content": "You have used all your tool calls. Do not call tools any more: write your final answer / report now."})

    return AgentOutcome(final_text, steps, in_tok, out_tok)


def make_context(session: Session, agent: str, run: Optional[dict] = None) -> ToolContext:
    def on_todos(todos: list) -> None:
        session.data["todos"] = todos
        session.emit("todos.updated", {"todos": todos})
        session.touch()

    return ToolContext(directory=Path(session.directory), session=session, agent=agent, run=run, on_todos=on_todos)
