"""Model Context Protocol client: connect MCP servers and use their tools in sessions.

Implemented on the wire protocol (JSON-RPC 2.0) rather than an SDK so it stays stable
across SDK releases and works with any event loop on Windows, macOS and Linux:

- transports: ``stdio`` (newline-delimited JSON over a child process), ``http``
  (Streamable HTTP: POST, JSON or SSE responses, ``Mcp-Session-Id``) and the legacy
  ``sse`` transport (GET event stream + POST endpoint);
- lifecycle: lazy connect on first use, ``initialize`` / ``notifications/initialized``,
  paginated ``tools/list``, ``tools/list_changed`` refresh, automatic reconnect when a
  server dies, clean shutdown (process tree killed);
- server requests answered: ``ping``, ``roots/list`` (the session folder);
- results normalised to text (structured content as JSON, images and binary resources
  summarised) so they fit the local model's context.

Best practices applied by the agent layer (``mcp_tools_for``): tools are namespaced
``mcp__<server>__<tool>``; above ``deferThreshold`` tools only two meta-tools are
exposed (``mcp_search`` + ``mcp_call``) so large servers do not flood the context;
permissions per server, read-only tools trusted, project ``.mcp.json`` servers need an
explicit approval before they ever start.
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .osutil import IS_WINDOWS, kill_tree

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "doing-ai-supercode", "title": "DOINg.AI SuperCode", "version": "4.0.0"}
NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


class McpError(Exception):
    pass


def safe_name(text: str) -> str:
    return NAME_RE.sub("_", text)[:40] or "x"


def qualified(server: str, tool: str) -> str:
    return f"mcp__{safe_name(server)}__{safe_name(tool)}"[:64]


@dataclass
class McpTool:
    server: str
    name: str
    description: str
    input_schema: dict
    annotations: dict = field(default_factory=dict)

    @property
    def qualified(self) -> str:
        return qualified(self.server, self.name)

    @property
    def read_only(self) -> bool:
        return bool(self.annotations.get("readOnlyHint"))


# ---------------------------------------------------------------------------
# transports


class _Transport:
    on_message: Callable[[dict], None]
    on_close: Callable[[str], None]

    async def start(self) -> None: ...
    async def send(self, message: dict) -> None: ...
    async def close(self) -> None: ...

    def stderr_tail(self) -> str:
        return ""


class StdioTransport(_Transport):
    def __init__(self, command: str, args: list[str], env: dict, cwd: Optional[str]):
        self.command, self.args, self.env, self.cwd = command, args, env, cwd
        self.proc: Optional[subprocess.Popen] = None
        self._stderr: deque[str] = deque(maxlen=60)
        self._write_lock = threading.Lock()

    async def start(self) -> None:
        exe = shutil.which(self.command) or self.command  # Windows: npx -> npx.cmd
        env = {**os.environ, **{k: str(v) for k, v in (self.env or {}).items()}}
        kwargs: dict = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen([exe, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, cwd=self.cwd or None, env=env, **kwargs)
        except OSError as e:
            raise McpError(f"cannot start '{self.command}': {e}") from e
        loop = asyncio.get_running_loop()

        def post(fn: Callable, arg: Any) -> bool:
            try:
                loop.call_soon_threadsafe(fn, arg)
                return True
            except RuntimeError:  # the event loop is gone (shutdown)
                return False

        def read_stdout() -> None:
            assert self.proc and self.proc.stdout
            try:
                for raw in iter(self.proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        self._stderr.append(f"[stdout] {line[:300]}")
                        continue
                    if not post(self.on_message, msg):
                        return
            except (OSError, ValueError):
                pass
            code = self.proc.wait()
            post(self.on_close, f"process exited with code {code}")

        def read_stderr() -> None:
            assert self.proc and self.proc.stderr
            try:
                for raw in iter(self.proc.stderr.readline, b""):
                    self._stderr.append(raw.decode("utf-8", "replace").rstrip()[:500])
            except (OSError, ValueError):
                pass

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()

    async def send(self, message: dict) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise McpError("server process is not running")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")

        def write() -> None:
            with self._write_lock:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()

        try:
            await asyncio.to_thread(write)
        except (OSError, ValueError) as e:
            raise McpError(f"write failed: {e}") from e

    async def close(self) -> None:
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass
            kill_tree(self.proc)

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)


def _sse_events(lines: list[str]) -> list[tuple[str, str]]:
    events, event, data = [], "message", []
    for line in lines:
        if line == "":
            if data:
                events.append((event, "\n".join(data)))
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        events.append((event, "\n".join(data)))
    return events


class HttpTransport(_Transport):
    """Streamable HTTP (MCP 2025-03-26 and later)."""

    def __init__(self, url: str, headers: dict, timeout: float):
        self.url, self.headers, self.timeout = url, headers or {}, timeout
        self.session_id: Optional[str] = None
        self.protocol: Optional[str] = None
        self.client: Optional[httpx.AsyncClient] = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15), follow_redirects=True)

    def _headers(self) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    async def send(self, message: dict) -> None:
        assert self.client
        task = asyncio.create_task(self._post(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if "id" not in message:  # notification: nothing to wait for
            return

    async def _post(self, message: dict) -> None:
        assert self.client
        try:
            async with self.client.stream("POST", self.url, json=message, headers=self._headers()) as res:
                if res.headers.get("mcp-session-id"):
                    self.session_id = res.headers["mcp-session-id"]
                if res.status_code == 202:
                    return
                if res.status_code >= 400:
                    body = (await res.aread()).decode("utf-8", "replace")[:400]
                    self._fail(message, f"HTTP {res.status_code}: {body}", expired=res.status_code == 404 and bool(self.session_id))
                    return
                ctype = res.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    buf: list[str] = []
                    async for line in res.aiter_lines():
                        buf.append(line.rstrip("\r"))
                        if line == "":
                            for _, data in _sse_events(buf):
                                self._deliver(data)
                            buf = []
                    for _, data in _sse_events(buf):
                        self._deliver(data)
                else:
                    self._deliver((await res.aread()).decode("utf-8", "replace"))
        except httpx.HTTPError as e:
            self._fail(message, f"{type(e).__name__}: {e}")

    def _deliver(self, data: str) -> None:
        if not data.strip():
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        for msg in payload if isinstance(payload, list) else [payload]:
            self.on_message(msg)

    def _fail(self, message: dict, error: str, expired: bool = False) -> None:
        if "id" in message:
            self.on_message({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": error}})
        if expired:
            self.on_close("session expired")

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self.client:
            if self.session_id:
                try:
                    await self.client.delete(self.url, headers=self._headers())
                except httpx.HTTPError:
                    pass
            await self.client.aclose()


class SseTransport(_Transport):
    """Legacy HTTP+SSE transport (MCP 2024-11-05): GET stream, POST to the announced endpoint."""

    def __init__(self, url: str, headers: dict, timeout: float):
        self.url, self.headers, self.timeout = url, headers or {}, timeout
        self.client: Optional[httpx.AsyncClient] = None
        self.endpoint: Optional[str] = None
        self._ready = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=15), follow_redirects=True)
        self._task = asyncio.create_task(self._listen())
        try:
            await asyncio.wait_for(self._ready.wait(), 20)
        except asyncio.TimeoutError as e:
            raise McpError("SSE server did not announce its endpoint") from e

    async def _listen(self) -> None:
        assert self.client
        try:
            async with self.client.stream("GET", self.url, headers={"Accept": "text/event-stream", **self.headers}) as res:
                if res.status_code >= 400:
                    self.on_close(f"HTTP {res.status_code}")
                    self._ready.set()
                    return
                buf: list[str] = []
                async for line in res.aiter_lines():
                    line = line.rstrip("\r")
                    buf.append(line)
                    if line != "":
                        continue
                    for event, data in _sse_events(buf):
                        if event == "endpoint":
                            self.endpoint = str(httpx.URL(self.url).join(data.strip()))
                            self._ready.set()
                        else:
                            try:
                                self.on_message(json.loads(data))
                            except json.JSONDecodeError:
                                pass
                    buf = []
        except httpx.HTTPError as e:
            self.on_close(f"{type(e).__name__}: {e}")
        finally:
            self._ready.set()
            self.on_close("event stream closed")

    async def send(self, message: dict) -> None:
        if not self.client or not self.endpoint:
            raise McpError("SSE transport not connected")
        res = await self.client.post(self.endpoint, json=message, headers={"Content-Type": "application/json", **self.headers},
                                     timeout=self.timeout)
        if res.status_code >= 400:
            raise McpError(f"HTTP {res.status_code}: {res.text[:300]}")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self.client:
            await self.client.aclose()


# ---------------------------------------------------------------------------
# connection


def config_hash(cfg: dict, directory: str) -> str:
    return hashlib.sha1(json.dumps([cfg, directory], sort_keys=True, default=str).encode()).hexdigest()


class McpConnection:
    def __init__(self, name: str, cfg: dict, directory: str):
        self.name, self.cfg, self.directory = name, cfg, directory
        self.hash = config_hash(cfg, directory)
        self.status = "idle"
        self.error = ""
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.tools: list[McpTool] = []
        self.instructions = ""
        self.transport: Optional[_Transport] = None
        self._ids = itertools.count(1)
        self._pending: dict[Any, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.log: deque[str] = deque(maxlen=40)

    @property
    def timeout(self) -> float:
        return float(self.cfg.get("timeout") or 120)

    def _make_transport(self) -> _Transport:
        kind = self.cfg.get("type") or ("http" if self.cfg.get("url") else "stdio")
        if kind == "stdio":
            if not self.cfg.get("command"):
                raise McpError("stdio server needs a command")
            cwd = self.cfg.get("cwd") or self.directory or None
            return StdioTransport(self.cfg["command"], [str(a) for a in self.cfg.get("args") or []], self.cfg.get("env") or {}, cwd)
        if not self.cfg.get("url"):
            raise McpError(f"{kind} server needs a url")
        if kind == "sse":
            return SseTransport(self.cfg["url"], self.cfg.get("headers") or {}, self.timeout)
        return HttpTransport(self.cfg["url"], self.cfg.get("headers") or {}, self.timeout)

    async def connect(self) -> None:
        async with self._lock:
            if self.status == "connected":
                return
            await self._close_transport()
            self.status, self.error = "connecting", ""
            try:
                self.transport = self._make_transport()
                self.transport.on_message = self._on_message
                self.transport.on_close = self._on_close
                await self.transport.start()
                init = await self._request("initialize", {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"roots": {"listChanged": False}},
                    "clientInfo": CLIENT_INFO,
                }, timeout=min(self.timeout, float(self.cfg.get("startupTimeout") or 60)))
                self.server_info = init.get("serverInfo") or {}
                self.capabilities = init.get("capabilities") or {}
                self.instructions = str(init.get("instructions") or "")[:2000]
                if isinstance(self.transport, HttpTransport):
                    self.transport.protocol = init.get("protocolVersion") or PROTOCOL_VERSION
                await self._notify("notifications/initialized")
                self.status = "connected"
                await self.refresh_tools()
            except Exception as e:  # noqa: BLE001 - recorded and surfaced in the UI
                self.status, self.error = "error", f"{e}"
                tail = self.transport.stderr_tail() if self.transport else ""
                if tail:
                    self.error += f"\n{tail[-800:]}"
                await self._close_transport()
                raise McpError(self.error) from e

    async def refresh_tools(self) -> None:
        tools: list[McpTool] = []
        cursor = None
        for _ in range(50):
            res = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            for t in res.get("tools") or []:
                tools.append(McpTool(self.name, str(t.get("name")), str(t.get("description") or "")[:1500],
                                     t.get("inputSchema") or {"type": "object", "properties": {}}, t.get("annotations") or {}))
            cursor = res.get("nextCursor")
            if not cursor:
                break
        disabled = set(self.cfg.get("disabledTools") or [])
        self.tools = [t for t in tools if t.name not in disabled]

    async def call_tool(self, tool: str, arguments: dict, timeout: Optional[float] = None) -> dict:
        if self.status != "connected":
            await self.connect()
        return await self._request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=timeout or self.timeout)

    async def list_resources(self) -> list[dict]:
        if self.status != "connected":
            await self.connect()
        if "resources" not in self.capabilities:
            return []
        res = await self._request("resources/list", {})
        return res.get("resources") or []

    async def read_resource(self, uri: str) -> dict:
        if self.status != "connected":
            await self.connect()
        return await self._request("resources/read", {"uri": uri})

    # -- JSON-RPC ----------------------------------------------------------
    async def _request(self, method: str, params: dict, timeout: Optional[float] = None) -> dict:
        if not self.transport:
            raise McpError("not connected")
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self.transport.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError as e:
            await self._notify("notifications/cancelled", {"requestId": rid, "reason": "timeout"})
            raise McpError(f"{method} timed out after {timeout or self.timeout:.0f}s") from e
        finally:
            self._pending.pop(rid, None)

    async def _notify(self, method: str, params: Optional[dict] = None) -> None:
        if self.transport:
            try:
                await self.transport.send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})
            except McpError:
                pass

    def _on_message(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
            fut = self._pending.get(msg["id"])
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"] or {}
                    fut.set_exception(McpError(f"{err.get('message', 'error')} (code {err.get('code')})"))
                else:
                    fut.set_result(msg.get("result") or {})
            return
        method = msg.get("method", "")
        if "id" in msg:  # a request from the server
            asyncio.ensure_future(self._answer(msg))
        elif method == "notifications/tools/list_changed":
            asyncio.ensure_future(self._safe_refresh())
        elif method == "notifications/message":
            params = msg.get("params") or {}
            self.log.append(f"[{params.get('level', 'info')}] {str(params.get('data'))[:300]}")

    async def _answer(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "ping":
            result: Any = {}
        elif method == "roots/list":
            uri = Path(self.directory).resolve().as_uri() if self.directory else None
            result = {"roots": [{"uri": uri, "name": Path(self.directory).name}] if uri else []}
        else:
            await self._send_raw({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"{method} not supported by this client"}})
            return
        await self._send_raw({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    async def _send_raw(self, msg: dict) -> None:
        if self.transport:
            try:
                await self.transport.send(msg)
            except McpError:
                pass

    async def _safe_refresh(self) -> None:
        try:
            await self.refresh_tools()
        except McpError:
            pass

    def _on_close(self, reason: str) -> None:
        if self.status in ("connected", "connecting"):
            self.status, self.error = "error", f"disconnected: {reason}"
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(McpError(f"connection closed: {reason}"))

    async def _close_transport(self) -> None:
        if self.transport:
            try:
                await self.transport.close()
            except Exception:  # noqa: BLE001
                pass
        self.transport = None

    async def close(self) -> None:
        await self._close_transport()
        self.status = "stopped"

    def describe(self) -> dict:
        return {"name": self.name, "status": self.status, "error": self.error, "server": self.server_info,
                "tools": [{"name": t.name, "description": t.description[:200], "readOnly": t.read_only} for t in self.tools],
                "resources": "resources" in self.capabilities, "log": list(self.log)[-10:]}


# ---------------------------------------------------------------------------
# result formatting


def format_result(result: dict) -> tuple[str, bool]:
    """CallToolResult -> (text for the model, is_error)."""
    parts: list[str] = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind in ("image", "audio"):
            size = len(item.get("data") or "") * 3 // 4
            parts.append(f"[{kind} {item.get('mimeType', '')}, {size} bytes - not shown]")
        elif kind == "resource":
            res = item.get("resource") or {}
            if "text" in res:
                parts.append(f"[resource {res.get('uri', '')}]\n{res['text']}")
            else:
                parts.append(f"[binary resource {res.get('uri', '')} {res.get('mimeType', '')}]")
        elif kind == "resource_link":
            parts.append(f"[resource link {item.get('uri', '')}: {item.get('name', '')}]")
        else:
            parts.append(json.dumps(item, ensure_ascii=False)[:2000])
    structured = result.get("structuredContent")
    if structured is not None and not parts:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=1))
    return ("\n".join(parts) or "(empty result)"), bool(result.get("isError"))


# ---------------------------------------------------------------------------
# manager


def project_servers(directory: str) -> dict[str, dict]:
    """Servers declared by the project (.mcp.json, Claude Code format)."""
    p = Path(directory or ".") / ".mcp.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for name, cfg in (data.get("mcpServers") or {}).items():
        if isinstance(cfg, dict):
            out[str(name)] = {"enabled": True, "permission": "ask", "trustReadOnly": True, **cfg, "source": "project"}
    return out


def effective_servers(cfg: dict, directory: str) -> dict[str, dict]:
    """Global servers + approved project servers, enabled only."""
    mcp_cfg = cfg.get("mcp") or {}
    servers = {n: {**s, "source": "user"} for n, s in (mcp_cfg.get("servers") or {}).items() if s.get("enabled", True)}
    approved = set(mcp_cfg.get("approvedProjectServers") or [])
    for name, s in project_servers(directory).items():
        if f"{Path(directory).resolve()}::{name}" in approved and name not in servers and s.get("enabled", True):
            servers[name] = s
    return servers


class McpManager:
    def __init__(self) -> None:
        self.connections: dict[str, McpConnection] = {}

    def _key(self, name: str, directory: str, cfg: dict) -> str:
        # Stdio servers depend on the session folder (cwd, roots); remote ones do not.
        kind = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
        return f"{name}@{directory}" if kind == "stdio" and not cfg.get("cwd") else name

    async def connection(self, name: str, cfg: dict, directory: str) -> McpConnection:
        key = self._key(name, directory, cfg)
        conn = self.connections.get(key)
        if conn and conn.hash != config_hash(cfg, directory):
            await conn.close()
            conn = None
        if conn is None:
            conn = McpConnection(name, cfg, directory)
            self.connections[key] = conn
        if conn.status != "connected":
            await conn.connect()
        return conn

    async def tools(self, servers: dict[str, dict], directory: str) -> tuple[list[McpTool], dict[str, str]]:
        """Tools of every reachable server; errors per server are returned, not raised."""
        tools: list[McpTool] = []
        errors: dict[str, str] = {}

        async def one(name: str, cfg: dict) -> None:
            try:
                conn = await self.connection(name, cfg, directory)
                tools.extend(conn.tools)
            except McpError as e:
                errors[name] = str(e)

        await asyncio.gather(*(one(n, c) for n, c in servers.items()))
        return tools, errors

    async def call(self, server: str, cfg: dict, directory: str, tool: str, arguments: dict) -> tuple[str, bool]:
        conn = await self.connection(server, cfg, directory)
        try:
            result = await conn.call_tool(tool, arguments)
        except McpError as e:
            if conn.status != "connected":  # the server died: one reconnect, one retry
                conn = await self.connection(server, cfg, directory)
                result = await conn.call_tool(tool, arguments)
            else:
                raise e
        return format_result(result)

    async def test(self, name: str, cfg: dict, directory: str) -> dict:
        conn = McpConnection(name, cfg, directory)
        try:
            await conn.connect()
            return {"ok": True, **conn.describe()}
        except McpError as e:
            return {"ok": False, **conn.describe(), "error": str(e)}
        finally:
            await conn.close()

    def status(self) -> list[dict]:
        return [c.describe() for c in self.connections.values()]

    async def shutdown(self) -> None:
        await asyncio.gather(*(c.close() for c in self.connections.values()), return_exceptions=True)
        self.connections.clear()


manager = McpManager()
