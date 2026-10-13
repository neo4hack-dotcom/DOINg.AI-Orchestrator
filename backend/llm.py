"""Local LLM client: Ollama, OpenAI-compatible servers and n8n webhooks.

Same three providers as NEXUS.AI, but with streaming and tool calling. Messages use
one internal shape and are converted per provider:

    {"role": "system" | "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx

DeltaFn = Callable[[str, str], Awaitable[None]]  # (kind: "text" | "reasoning", chunk)


class LLMError(Exception):
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class ToolsUnsupported(LLMError):
    """The server or model rejected native tool calling."""


class ContextOverflow(LLMError):
    """The request is larger than the model's context window."""


_OVERFLOW_HINTS = ("context length", "context_length", "maximum context", "too many tokens", "context window",
                   "exceeds the context", "prompt is too long", "input is too long", "num_ctx")


def _http_error(provider: str, status: int, text: str) -> LLMError:
    low = text.lower()
    if any(h in low for h in _OVERFLOW_HINTS):
        return ContextOverflow(f"{provider}: context overflow ({text[:300]})")
    transient = status in (408, 409, 425, 429) or status >= 500 or "overloaded" in low or "busy" in low
    return LLMError(f"{provider} HTTP {status}: {text[:500]}", transient=transient)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(tc.get("arguments", {})))
    return total


# --------------------------------------------------------------------------
# <think> splitting: reasoning models inline their chain of thought.


class ThinkSplitter:
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.buf += chunk
        out: list[tuple[str, str]] = []
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                if idx:
                    out.append(("reasoning" if self.inside else "text", self.buf[:idx]))
                self.buf = self.buf[idx + len(tag):]
                self.inside = not self.inside
                continue
            # Keep a possible partial tag at the end for the next chunk.
            keep = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    keep = n
                    break
            emit = self.buf[: len(self.buf) - keep]
            if emit:
                out.append(("reasoning" if self.inside else "text", emit))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buf = self.buf, ""
        return [("reasoning" if self.inside else "text", rest)] if rest else []


# --------------------------------------------------------------------------
# Text tool protocol for models / servers without native tool calling.

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
FENCED_CALL_RE = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.S)


def _loads_lenient(raw: str) -> Optional[dict]:
    raw = raw.strip()
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        pass
    # Trim to the outermost braces (models add prose or trailing commas).
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    snippet = re.sub(r",\s*([}\]])", r"\1", raw[start : end + 1])
    try:
        val = json.loads(snippet)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        return None


def _as_call(obj: dict, known: set[str]) -> Optional[ToolCall]:
    name = obj.get("name") or obj.get("tool") or (obj.get("function") or {}).get("name")
    args = obj.get("arguments", obj.get("parameters", obj.get("args", (obj.get("function") or {}).get("arguments"))))
    if isinstance(args, str):
        args = _loads_lenient(args) or {}
    if not isinstance(name, str) or (known and name not in known):
        return None
    return ToolCall(id=f"call_{uuid.uuid4().hex[:10]}", name=name, arguments=args if isinstance(args, dict) else {})


def parse_text_tool_calls(text: str, known: set[str]) -> tuple[str, list[ToolCall]]:
    """Extract ``<tool_call>{json}</tool_call>`` blocks; returns (clean text, calls)."""
    calls: list[ToolCall] = []
    for m in TOOL_CALL_RE.finditer(text):
        obj = _loads_lenient(m.group(1))
        call = _as_call(obj, known) if obj else None
        if call:
            calls.append(call)
    clean = TOOL_CALL_RE.sub("", text)
    if not calls:
        # Some models wrap the call in a fenced json block instead.
        for m in FENCED_CALL_RE.finditer(text):
            obj = _loads_lenient(m.group(1))
            call = _as_call(obj, known) if obj and ("name" in obj or "tool" in obj) else None
            if call:
                calls.append(call)
                clean = clean.replace(m.group(0), "")
    return clean.strip(), calls


def tool_protocol_prompt(tools: list[dict]) -> str:
    lines = [
        "# Tools",
        "You can call tools. To call one, write a block exactly like this (one block per call):",
        "<tool_call>",
        '{"name": "<tool name>", "arguments": {<arguments as JSON>}}',
        "</tool_call>",
        "After your tool calls, stop and wait: results come back in <tool_result> blocks.",
        "When you need no more tools, answer normally without any <tool_call> block.",
        "",
        "Available tools:",
    ]
    for t in tools:
        fn = t["function"]
        lines.append(f"- {fn['name']}: {fn['description']}")
        lines.append(f"  parameters: {json.dumps(fn['parameters'], ensure_ascii=False)}")
    return "\n".join(lines)


def flatten_for_text_protocol(messages: list[dict]) -> list[dict]:
    """Rewrite tool traffic as plain text turns."""
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "assistant":
            text = m.get("content") or ""
            for tc in m.get("tool_calls") or []:
                block = json.dumps({"name": tc["name"], "arguments": tc["arguments"]}, ensure_ascii=False)
                text += f"\n<tool_call>\n{block}\n</tool_call>"
            out.append({"role": "assistant", "content": text.strip()})
        elif role == "tool":
            body = f'<tool_result name="{m.get("name", "")}">\n{m.get("content", "")}\n</tool_result>'
            if out and out[-1]["role"] == "user" and out[-1].get("_tool"):
                out[-1]["content"] += "\n" + body
            else:
                out.append({"role": "user", "content": body, "_tool": True})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    for m in out:
        m.pop("_tool", None)
    return out


# --------------------------------------------------------------------------
# Providers


def _base(url: str, default: str) -> str:
    return (url or default).strip().rstrip("/")


def openai_chat_url(base_url: str) -> str:
    url = _base(base_url, "http://localhost:8000")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


def openai_models_url(base_url: str) -> str:
    url = _base(base_url, "http://localhost:8000")
    url = re.sub(r"/chat/completions$", "", url)
    return url + "/models" if url.endswith("/v1") else url + "/v1/models"


def _timeout(cfg: dict) -> httpx.Timeout:
    return httpx.Timeout(float(cfg.get("timeout") or 600), connect=10.0)


def _looks_like_tool_refusal(status: int, body: str) -> bool:
    low = body.lower()
    return status in (400, 404, 422, 500, 501) and (
        "tool" in low or "function" in low
    ) and ("support" in low or "not allowed" in low or "invalid" in low or "unknown" in low or "unrecognized" in low)


async def _emit(on_delta: Optional[DeltaFn], splitter: ThinkSplitter, chunk: str, acc: dict) -> None:
    for kind, piece in splitter.feed(chunk):
        acc[kind] += piece
        if on_delta and piece:
            await on_delta(kind, piece)


async def _flush(on_delta: Optional[DeltaFn], splitter: ThinkSplitter, acc: dict) -> None:
    for kind, piece in splitter.flush():
        acc[kind] += piece
        if on_delta and piece:
            await on_delta(kind, piece)


async def _chat_ollama(cfg, model, messages, tools, on_delta, json_mode, temperature) -> ChatResult:
    url = _base(cfg.get("baseUrl"), "http://localhost:11434") + "/api/chat"
    wire = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            wire.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": [{"function": {"name": tc["name"], "arguments": tc["arguments"]}} for tc in m["tool_calls"]],
            })
        elif m["role"] == "tool":
            wire.append({"role": "tool", "content": m.get("content") or "", "tool_name": m.get("name", "")})
        else:
            wire.append({"role": m["role"], "content": m.get("content") or ""})
    body: dict[str, Any] = {
        "model": model,
        "messages": wire,
        "stream": True,
        "options": {"temperature": temperature, "num_ctx": int(cfg.get("contextWindow") or 16384)},
    }
    if tools:
        body["tools"] = tools
    if json_mode:
        body["format"] = "json"

    acc = {"text": "", "reasoning": ""}
    splitter = ThinkSplitter()
    calls: list[ToolCall] = []
    p_tok = c_tok = 0
    async with httpx.AsyncClient(timeout=_timeout(cfg)) as client:
        async with client.stream("POST", url, json=body) as res:
            if res.status_code >= 400:
                text = (await res.aread()).decode("utf-8", "replace")
                if tools and "does not support tools" in text:
                    raise ToolsUnsupported(text)
                raise _http_error("Ollama", res.status_code, text)
            async for line in res.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise _http_error("Ollama", 500, str(data["error"]))
                msg = data.get("message") or {}
                if msg.get("thinking"):
                    acc["reasoning"] += msg["thinking"]
                    if on_delta:
                        await on_delta("reasoning", msg["thinking"])
                if msg.get("content"):
                    await _emit(on_delta, splitter, msg["content"], acc)
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        args = _loads_lenient(args) or {}
                    calls.append(ToolCall(id=f"call_{uuid.uuid4().hex[:10]}", name=fn.get("name", ""), arguments=args or {}))
                if data.get("done"):
                    p_tok = data.get("prompt_eval_count") or 0
                    c_tok = data.get("eval_count") or 0
    await _flush(on_delta, splitter, acc)
    return ChatResult(acc["text"], calls, acc["reasoning"], p_tok, c_tok)


async def _chat_openai(cfg, model, messages, tools, on_delta, json_mode, temperature) -> ChatResult:
    url = openai_chat_url(cfg.get("baseUrl"))
    headers = {"Content-Type": "application/json"}
    if cfg.get("apiKey"):
        headers["Authorization"] = f"Bearer {cfg['apiKey']}"
    wire = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            wire.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                    for tc in m["tool_calls"]
                ],
            })
        elif m["role"] == "tool":
            wire.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m.get("content") or ""})
        else:
            wire.append({"role": m["role"], "content": m.get("content") or ""})
    body: dict[str, Any] = {"model": model, "messages": wire, "stream": True, "temperature": temperature}
    if tools:
        body["tools"] = tools

    acc = {"text": "", "reasoning": ""}
    splitter = ThinkSplitter()
    partial: dict[int, dict] = {}
    p_tok = c_tok = 0
    async with httpx.AsyncClient(timeout=_timeout(cfg)) as client:
        async with client.stream("POST", url, json=body, headers=headers) as res:
            if res.status_code >= 400:
                text = (await res.aread()).decode("utf-8", "replace")
                if tools and _looks_like_tool_refusal(res.status_code, text):
                    raise ToolsUnsupported(text)
                raise _http_error("OpenAI-compatible", res.status_code, text)
            ctype = res.headers.get("content-type", "")
            if "text/event-stream" not in ctype:
                # Server ignored stream=true: plain JSON body.
                data = json.loads(await res.aread())
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                if msg.get("reasoning_content"):
                    acc["reasoning"] += msg["reasoning_content"]
                await _emit(on_delta, splitter, msg.get("content") or data.get("content") or "", acc)
                for i, tc in enumerate(msg.get("tool_calls") or []):
                    partial[i] = {"id": tc.get("id"), "name": tc["function"]["name"], "args": tc["function"].get("arguments") or ""}
                usage = data.get("usage") or {}
                p_tok, c_tok = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            else:
                async for line in res.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise _http_error("OpenAI-compatible", 500, json.dumps(data["error"]))
                    usage = data.get("usage") or {}
                    if usage:
                        p_tok = usage.get("prompt_tokens", p_tok)
                        c_tok = usage.get("completion_tokens", c_tok)
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        rc = delta.get("reasoning_content") or delta.get("reasoning")
                        if rc:
                            acc["reasoning"] += rc
                            if on_delta:
                                await on_delta("reasoning", rc)
                        if delta.get("content"):
                            await _emit(on_delta, splitter, delta["content"], acc)
                        for tc in delta.get("tool_calls") or []:
                            slot = partial.setdefault(tc.get("index", len(partial)), {"id": None, "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                args = fn["arguments"]
                                slot["args"] += args if isinstance(args, str) else json.dumps(args)
    await _flush(on_delta, splitter, acc)
    calls = [
        ToolCall(id=s["id"] or f"call_{uuid.uuid4().hex[:10]}", name=s["name"], arguments=_loads_lenient(s["args"] or "{}") or {})
        for _, s in sorted(partial.items())
        if s["name"]
    ]
    return ChatResult(acc["text"], calls, acc["reasoning"], p_tok, c_tok)


async def _chat_n8n(cfg, model, messages, tools, on_delta, json_mode, temperature) -> ChatResult:
    url = (cfg.get("baseUrl") or "").strip()
    if not url:
        raise LLMError("n8n webhook URL missing")
    headers = {"Content-Type": "application/json"}
    if cfg.get("apiKey"):
        headers["Authorization"] = cfg["apiKey"]
    prompt = "\n\n".join(f"[{m['role'].upper()}]\n{m.get('content') or ''}" for m in messages)
    body = {"prompt": prompt, "messages": messages, "model": model, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    async with httpx.AsyncClient(timeout=_timeout(cfg)) as client:
        res = await client.post(url, json=body, headers=headers)
    if res.status_code >= 400:
        raise _http_error("n8n", res.status_code, res.text)
    try:
        data = res.json()
    except ValueError:
        data = res.text
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, str):
        text = data
    else:
        text = data.get("output") or data.get("text") or data.get("response") or data.get("content") or json.dumps(data)
    acc = {"text": "", "reasoning": ""}
    splitter = ThinkSplitter()
    await _emit(on_delta, splitter, text, acc)
    await _flush(on_delta, splitter, acc)
    return ChatResult(acc["text"], [], acc["reasoning"], messages_tokens(messages), estimate_tokens(text))


async def chat(
    cfg: dict,
    *,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    on_delta: Optional[DeltaFn] = None,
    json_mode: bool = False,
    temperature: Optional[float] = None,
) -> ChatResult:
    provider = cfg.get("provider", "ollama")
    temp = float(cfg.get("temperature", 0.2) if temperature is None else temperature)
    try:
        if provider == "ollama":
            res = await _chat_ollama(cfg, model, messages, tools, on_delta, json_mode, temp)
        elif provider == "openai_compatible":
            res = await _chat_openai(cfg, model, messages, tools, on_delta, json_mode, temp)
        elif provider == "n8n":
            res = await _chat_n8n(cfg, model, messages, None, on_delta, json_mode, temp)
        else:
            raise LLMError(f"Unsupported provider: {provider}")
    except httpx.ConnectError as e:
        raise LLMError(f"Cannot reach {provider} at {cfg.get('baseUrl')}: {e}", transient=True) from e
    except httpx.TimeoutException as e:
        raise LLMError(f"{provider} timed out: {e}", transient=True) from e
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
        raise LLMError(f"{provider} connection dropped: {e}", transient=True) from e
    if not res.prompt_tokens:
        res.prompt_tokens = messages_tokens(messages)
    if not res.completion_tokens:
        res.completion_tokens = estimate_tokens(res.text + res.reasoning)
    return res


async def test_connection(cfg: dict) -> dict:
    provider = cfg.get("provider", "ollama")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            if provider == "ollama":
                res = await client.get(_base(cfg.get("baseUrl"), "http://localhost:11434") + "/api/tags")
                if res.status_code >= 400:
                    return {"ok": False, "message": f"HTTP {res.status_code}"}
                models = [m.get("name") for m in res.json().get("models", [])]
                return {"ok": True, "models": models, "message": f"Online. {len(models)} models."}
            if provider == "openai_compatible":
                headers = {"Authorization": f"Bearer {cfg['apiKey']}"} if cfg.get("apiKey") else {}
                res = await client.get(openai_models_url(cfg.get("baseUrl")), headers=headers)
                if res.status_code >= 400:
                    return {"ok": False, "message": f"HTTP {res.status_code}"}
                models = [m.get("id") for m in res.json().get("data", [])]
                return {"ok": True, "models": models, "message": f"Online. {len(models)} models."}
        out = await chat(cfg, model=cfg.get("model", ""), messages=[{"role": "user", "content": "ping"}])
        return {"ok": True, "models": [], "message": f"Webhook replied: {out.text[:80]}"}
    except Exception as e:  # noqa: BLE001 - surfaced to the settings screen
        return {"ok": False, "message": str(e)}
