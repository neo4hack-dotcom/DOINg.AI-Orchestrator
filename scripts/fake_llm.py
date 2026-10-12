"""A scripted fake local LLM for demos and tests: no GPU needed.

Speaks the Ollama (/api/chat, /api/tags) and OpenAI-compatible (/v1/chat/completions,
/v1/models) wire formats, streams its answers, and plays each role of the app
(build agent, savvy orchestrator, savvy workers) with canned but valid moves.

    python scripts/fake_llm.py --port 11500            # native tool calls
    python scripts/fake_llm.py --port 11500 --no-tools # forces the <tool_call> text protocol

Then point Settings > LLM at http://localhost:11500 (Ollama or OpenAI-compatible).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
STATE = {"no_tools": False, "delay": 0.02, "plan_mode": "simple", "fail_next": 0, "overflow_next": 0}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "output"


def _tool_results(messages: list[dict]) -> int:
    return sum(
        1 for m in messages
        if m.get("role") == "tool" or (m.get("role") == "user" and str(m.get("content", "")).startswith("<tool_result"))
    )


def _assistant_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant")


GREETING_PY = 'def greet(name):\n    return f"Hello, {name}!"\n'
CHECK_PY = "python3 -c \"import greeting; assert greeting.greet('x') == 'Hello, x!'\""


def _task(title, tier, size, complexity, files, after, brief, verify_cmd=""):
    return {"title": title, "tier": tier, "size": size, "complexity": complexity, "files": files, "after": after,
            "brief": brief, "verify": "check it", "verifyCommand": verify_cmd}


def brain(messages: list[dict]) -> tuple[str, list[dict]]:
    """Return (text, tool_calls) for the conversation."""
    system = str(messages[0].get("content", "")) if messages and messages[0].get("role") == "system" else ""
    users = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
    first_user = users[0] if users else ""
    turns = _assistant_turns(messages)

    if "Convert the input into ONE valid JSON object" in system:
        return json.dumps({"verdict": "accept", "defects": [], "tier": "", "note": "repaired"}), []

    if "structured progress checkpoints" in system:
        return "## Objective\n- keep going\n## Work State\n### Completed\n- earlier steps", []

    if "You evaluate whether a coding agent has completed" in system:
        return json.dumps({"complete": True, "reason": "the goal is met", "next_instruction": None}), []

    if "Phase: PLAN" in system:
        if turns == 0:
            return "Let me look at the workspace first.", [{"name": "list", "arguments": {"path": "."}}]
        base = {"title": "Greeting feature", "summary": "Create a greeting module, then document it.",
                "touchesDesign": False, "design": "", "questions": [],
                "spec": "# SPEC\n\nA tiny demo project with a greeting.\n" if "NEW PROJECT" in system else ""}
        if STATE.get("plan_mode") == "modular":
            plan = {**base, "mode": "modular", "verifyCommands": [CHECK_PY], "tasks": [], "modules": [
                {"id": "core", "name": "Core greeting", "goal": "greet() function", "files": ["greeting.py"],
                 "contracts": "greet(name: str) -> str returns 'Hello, <name>!'", "dependsOn": [], "verifyCommand": CHECK_PY},
                {"id": "docs", "name": "Documentation", "goal": "document greet", "files": ["GREETING.md"],
                 "contracts": "", "dependsOn": ["core"], "verifyCommand": ""},
            ]}
        else:
            plan = {**base, "mode": "simple", "verifyCommands": [], "modules": [], "tasks": [
                _task("Create greeting module", "light", "S", 1, ["greeting.py"], [],
                      "Create greeting.py with a function greet(name) returning 'Hello, <name>!'.", CHECK_PY),
                _task("Document greeting", "medium", "M", 2, ["GREETING.md"], [1],
                      "Create GREETING.md explaining greet(name).", "test -f GREETING.md"),
            ]}
        return "```json\n" + json.dumps(plan, indent=2) + "\n```", []

    if "planning ONE module" in system:
        if "## Module core" in first_user:
            return json.dumps({"tasks": [_task("Create greeting module", "light", "S", 1, ["greeting.py"], [],
                                               "Create greeting.py with greet(name).", CHECK_PY)]}), []
        return json.dumps({"tasks": [_task("Document greeting", "medium", "M", 2, ["GREETING.md"], [],
                                           "Create GREETING.md explaining greet(name).", "test -f GREETING.md")]}), []

    if "too large or too complex for one worker" in system:
        return json.dumps({"tasks": [_task("Part one", "medium", "M", 3, [], [], "first half"),
                                     _task("Part two", "medium", "M", 3, [], [1], "second half")]}), []

    if "phase VERIFY" in system:
        return json.dumps({"tasks": []}), []

    if "phase REVIEW" in system:
        if turns == 0:
            m = re.search(r"\+\+\+ b/(\S+)", first_user)
            if m:
                return "Checking the change.", [{"name": "read", "arguments": {"path": m.group(1)}}]
        return json.dumps({"verdict": "accept", "defects": [], "tier": "", "note": "Diff matches the brief."}), []

    if "phase CLOSE" in system:
        return (
            "### Done\n\n- `greeting.py`: `greet(name)` added.\n- `GREETING.md`: usage documented.\n\n"
            "**Verified**: the import check passes.\n\n**Left out**: nothing.\n\n"
            "Tiers that ran: savvy-light, savvy-medium."
        ), []

    if "worker in the savvy-flow pipeline" in system:
        title = (re.search(r"# Task: (.+)", first_user) or [None, "task"])[1].strip()
        low = title.lower()
        target = "greeting.py" if "module" in low else ("GREETING.md" if "document" in low else f"{_slug(title)}.txt")
        content = GREETING_PY if target.endswith(".py") else f"# {title}\n\nUse `greet(name)` to say hello.\n"
        if turns == 0:
            return "", [
                {"name": "step", "arguments": {"done": 0, "total": 2, "note": "plan"}},
                {"name": "read", "arguments": {"path": target}},
                {"name": "write", "arguments": {"path": target, "content": content}},
            ]
        if turns == 1:
            return "", [{"name": "step", "arguments": {"done": 2, "total": 2, "note": "verified"}}]
        return f"1. Result: done.\n2. Files changed:\n- {target}: created.\n3. Verification: file written and re-read.\n4. Open questions: none.", []

    # Build / plan agents: a tool call for a fresh question, an answer after results.
    real_users = [u for u in users if not u.startswith("<tool_result")]
    last_user = real_users[-1] if real_users else ""
    last = messages[-1] if messages else {}
    after_tool = last.get("role") == "tool" or str(last.get("content", "")).startswith("<tool_result")
    if not after_tool:
        if re.search(r"\brun\b", last_user.lower()) and "READ-ONLY" not in system:
            return "Running it.", [{"name": "bash", "arguments": {"command": "printf 'hello from bash\\n'"}}]
        return "I'll look around first.", [{"name": "list", "arguments": {"path": "."}}]
    return f"Here is what I found. You said: **{last_user[:80]}**\n\n- The workspace is listed above.\n- Nothing else to do.", []


async def _stream_text(text: str):
    step = 6
    for i in range(0, len(text), step):
        yield text[i : i + step]
        if STATE["delay"]:
            await asyncio.sleep(STATE["delay"])


def _as_text_protocol(text: str, calls: list[dict]) -> str:
    for c in calls:
        text += "\n<tool_call>\n" + json.dumps(c) + "\n</tool_call>"
    return text


@app.get("/api/tags")
async def tags():
    return {"models": [{"name": "fake-coder:7b"}, {"name": "fake-coder:32b"}]}


@app.get("/v1/models")
async def models():
    return {"data": [{"id": "fake-coder:7b"}, {"id": "fake-coder:32b"}]}


@app.post("/api/chat")
async def ollama_chat(req: Request):
    body = await req.json()
    if STATE["fail_next"] > 0:
        STATE["fail_next"] -= 1
        return JSONResponse({"error": "model is overloaded, try again"}, status_code=503)
    if STATE["overflow_next"] > 0:
        STATE["overflow_next"] -= 1
        return JSONResponse({"error": "the input exceeds the context length of the model"}, status_code=400)
    if body.get("tools") and STATE["no_tools"]:
        return JSONResponse({"error": f"{body.get('model')} does not support tools"}, status_code=400)
    text, calls = brain(body["messages"])
    if not body.get("tools"):
        text, calls = _as_text_protocol(text, calls), []

    async def gen():
        async for chunk in _stream_text(text):
            yield json.dumps({"message": {"role": "assistant", "content": chunk}, "done": False}) + "\n"
        if calls:
            yield json.dumps({"message": {"role": "assistant", "content": "", "tool_calls": [{"function": c} for c in calls]}, "done": False}) + "\n"
        yield json.dumps({"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 900, "eval_count": 120}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/v1/chat/completions")
async def openai_chat(req: Request):
    body = await req.json()
    if STATE["fail_next"] > 0:
        STATE["fail_next"] -= 1
        return JSONResponse({"error": "model is overloaded, try again"}, status_code=503)
    if STATE["overflow_next"] > 0:
        STATE["overflow_next"] -= 1
        return JSONResponse({"error": "the input exceeds the context length of the model"}, status_code=400)
    if body.get("tools") and STATE["no_tools"]:
        return JSONResponse({"error": {"message": "tools are not supported by this server"}}, status_code=400)
    text, calls = brain(body["messages"])
    if not body.get("tools"):
        text, calls = _as_text_protocol(text, calls), []

    async def gen():
        async for chunk in _stream_text(text):
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": chunk}}]}) + "\n\n"
        for i, c in enumerate(calls):
            args = json.dumps(c["arguments"])
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": f"call_{uuid.uuid4().hex[:8]}", "function": {"name": c["name"], "arguments": args[: len(args) // 2]}}]}}]}) + "\n\n"
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "function": {"arguments": args[len(args) // 2 :]}}]}}]}) + "\n\n"
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 120}}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11500)
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--delay", type=float, default=0.02)
    a = parser.parse_args()
    STATE["no_tools"] = a.no_tools
    STATE["delay"] = a.delay
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
