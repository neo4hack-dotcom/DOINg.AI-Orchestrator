"""MCP servers as agent tools: dynamic tool definitions, deferred loading, permissions.

Best practices applied (Claude Code / OpenCode / MCP spec):
- tools are namespaced ``mcp__<server>__<tool>`` and their schemas trimmed (no ``$schema``,
  long descriptions cut) so a local model's context is not flooded;
- above ``mcp.deferThreshold`` tools the agent gets three meta-tools instead
  (``mcp_search`` -> schemas on demand, ``mcp_call``, ``mcp_resources``): a tool-search
  pattern that keeps the prompt prefix stable and small;
- read-only mode (plan agent, orchestrator) only ever sees tools annotated ``readOnlyHint``;
- per-server permission (ask / allow / deny), read-only tools trusted by default, "always"
  grants per server, auto mode honoured; deny always wins;
- a project's ``.mcp.json`` servers never start before the user approved them;
- connection failures are reported once per session, never fatal for the turn.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .config import load_config
from .mcp_client import McpError, McpTool, effective_servers, manager, project_servers
from .tools import ToolContext, ToolDef, ToolError, ToolResult

META_TOOLS = ("mcp_search", "mcp_call", "mcp_resources")


def clean_schema(schema: dict, max_desc: int = 300) -> dict:
    """A smaller, provider-friendly copy of an MCP input schema."""

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("$schema", "$id", "examples", "default") and node is not schema.get("properties"):
                    continue
                if k == "description" and isinstance(v, str):
                    out[k] = v[:max_desc]
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    out = walk(schema or {})
    out.pop("title", None)
    if out.get("type") != "object":
        out = {"type": "object", "properties": out.get("properties") or {}, **({"required": out["required"]} if out.get("required") else {})}
    out.setdefault("properties", {})
    return out


REA_NAME = "rea"


def servers_for(cfg: dict, directory: str) -> dict[str, dict]:
    """User servers + approved project servers + the REA server when the reverse layer is on."""
    servers = effective_servers(cfg, directory)
    rev = cfg.get("reverse") or {}
    if rev.get("enabled") and rev.get("acknowledged") and REA_NAME not in servers:
        servers[REA_NAME] = {**rea_server(rev), "source": "reverse"}
    return servers


def session_servers(cfg: dict, session) -> dict[str, dict]:
    disabled = set(session.data.get("mcpDisabled") or [])
    return {n: s for n, s in servers_for(cfg, session.directory).items() if n not in disabled}


def decision(cfg: dict, servers: dict, server: str, read_only: bool, grants: list) -> str:
    """allow | ask | deny for one MCP call (before the auto-mode upgrade)."""
    srv = servers.get(server) or {}
    value = srv.get("permission") or "ask"
    if value == "deny":
        return "deny"
    if value == "allow" or (read_only and srv.get("trustReadOnly", True)):
        return "allow"
    if f"mcp:{server}" in grants or "mcp" in grants:
        return "allow"
    glob = cfg["permissions"].get("mcp", "ask")  # a server left on "ask" inherits the global value
    return glob if glob in ("allow", "deny") else "ask"


def _score(tool: McpTool, words: list[str]) -> int:
    hay = f"{tool.server} {tool.name} {tool.description}".lower()
    name = f"{tool.server} {tool.name}".lower()
    return sum((3 if w in name else 0) + (1 if w in hay else 0) for w in words)


def _schema_line(tool: McpTool, max_desc: int) -> str:
    schema = json.dumps(clean_schema(tool.input_schema, 160), ensure_ascii=False, separators=(",", ":"))
    ro = " [read-only]" if tool.read_only else ""
    return f"- server={tool.server} tool={tool.name}{ro}\n  {tool.description[:max_desc]}\n  arguments schema: {schema[:2500]}"


class McpBundle:
    """The MCP part of one agent run."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolDef] = {}
        self.system = ""


async def prepare(session, read_only: bool, notify) -> McpBundle:
    """Connect the session's servers (lazily) and build the tools for one agent run.

    ``notify(text)`` reports a problem in the transcript (called at most once per problem and session)."""
    bundle = McpBundle()
    cfg = load_config()
    mcp_cfg = cfg["mcp"]
    servers = session_servers(cfg, session)
    _announce_project_servers(session, cfg, notify)
    if not servers:
        return bundle
    tools, errors = await manager.tools(servers, session.directory)
    shown = session.data.setdefault("mcpErrorsShown", [])
    for name, err in errors.items():
        key = f"{name}:{err[:80]}"
        if key not in shown:
            shown.append(key)
            notify(f"MCP server '{name}' is unavailable: {err.splitlines()[0][:300]}")
    if read_only:
        tools = [t for t in tools if t.read_only]
    if not tools:
        return bundle
    directory = session.directory
    max_desc = int(mcp_cfg.get("maxDescriptionChars") or 400)
    by_key = {(t.server, t.name): t for t in tools}

    async def call(server: str, name: str, args: dict) -> ToolResult:
        try:
            text, is_error = await manager.call(server, servers[server], directory, name, args)
        except McpError as e:
            raise ToolError(f"MCP {server}/{name} failed: {e}") from e
        if is_error:
            raise ToolError(f"MCP {server}/{name} returned an error:\n{text}")
        session.data.setdefault("mcpCalls", {}).setdefault(server, 0)
        session.data["mcpCalls"][server] += 1
        return ToolResult(text, f"{server} · {name}", {"mcp": {"server": server, "tool": name}})

    threshold = int(mcp_cfg.get("deferThreshold") or 0)
    lines = []
    for name in servers:
        own = [t for t in tools if t.server == name]
        if not own:
            continue
        conn = next((c for c in manager.connections.values() if c.name == name and c.status == "connected"), None)
        line = f"- {name}: {len(own)} tool(s)"
        if threshold and len(tools) > threshold:
            line += ": " + ", ".join(t.name for t in own)[:500]
        if conn and conn.instructions:
            line += "\n  Server instructions: " + re.sub(r"\s+", " ", conn.instructions)[:400]
        lines.append(line)

    if threshold and len(tools) > threshold:
        async def t_search(_ctx: ToolContext, args: dict) -> ToolResult:
            words = [w for w in re.findall(r"[\w-]+", str(args.get("query") or "").lower()) if len(w) > 1]
            pool = [t for t in tools if not args.get("server") or t.server == args.get("server")]
            ranked = sorted(pool, key=lambda t: -_score(t, words)) if words else pool
            hits = [t for t in ranked if not words or _score(t, words) > 0][: int(args.get("limit") or 6)]
            if not hits:
                return ToolResult("No matching MCP tool. Servers and tools:\n" + "\n".join(lines), "no match")
            return ToolResult("\n".join(_schema_line(t, max_desc) for t in hits), f"{len(hits)} tool(s)")

        async def t_call(_ctx: ToolContext, args: dict) -> ToolResult:
            server, name = str(args.get("server") or ""), str(args.get("tool") or "")
            if (server, name) not in by_key:
                raise ToolError(f"Unknown MCP tool {server}/{name}. Use mcp_search first.")
            arguments = args.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as e:
                    raise ToolError(f"arguments must be a JSON object: {e}") from e
            return await call(server, name, arguments)

        bundle.tools["mcp_search"] = ToolDef(
            "mcp_search", "Search the connected MCP servers' tools by keywords; returns the matching tools with their argument schemas. Call it before mcp_call.",
            {"type": "object", "properties": {"query": {"type": "string"}, "server": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            t_search, meta={"mcp": True})
        bundle.tools["mcp_call"] = ToolDef(
            "mcp_call", "Call a tool of a connected MCP server (find it and its schema with mcp_search first).",
            {"type": "object", "properties": {"server": {"type": "string"}, "tool": {"type": "string"},
                                              "arguments": {"type": "object", "description": "Arguments matching the tool's schema"}},
             "required": ["server", "tool"]},
            t_call, "mcp", meta={"mcp": True, "dynamic": True, "readOnly": {k: v.read_only for k, v in by_key.items()}})
        header = ("MCP servers are connected. Their tools are not listed individually: find one with mcp_search "
                  "(keywords), then run it with mcp_call.")
    else:
        for t in tools:
            async def run(_ctx: ToolContext, args: dict, _t: McpTool = t) -> ToolResult:
                return await call(_t.server, _t.name, args)

            desc = (t.description or t.name)[:max_desc] + (" [read-only]" if t.read_only else "")
            bundle.tools[t.qualified] = ToolDef(t.qualified, f"[MCP {t.server}] {desc}", clean_schema(t.input_schema), run, "mcp",
                                                meta={"mcp": True, "server": t.server, "tool": t.name, "readOnly": t.read_only})
        header = "MCP servers are connected; their tools are named mcp__<server>__<tool>."

    with_resources = [n for n in servers if any(c.name == n and "resources" in c.capabilities for c in manager.connections.values())]
    if with_resources:
        async def t_resources(_ctx: ToolContext, args: dict) -> ToolResult:
            server = str(args.get("server") or "")
            if server not in with_resources:
                raise ToolError(f"Servers with resources: {', '.join(with_resources)}")
            conn = await manager.connection(server, servers[server], directory)
            try:
                if args.get("uri"):
                    res = await conn.read_resource(str(args["uri"]))
                    out = []
                    for c in res.get("contents") or []:
                        out.append(c.get("text") if "text" in c else f"[binary {c.get('mimeType', '')}, not shown]")
                    return ToolResult("\n".join(out) or "(empty)", str(args["uri"])[:80])
                items = await conn.list_resources()
            except McpError as e:
                raise ToolError(str(e)) from e
            text = "\n".join(f"- {r.get('uri')} {r.get('name', '')} {r.get('mimeType', '')} {str(r.get('description') or '')[:120]}"
                             for r in items[:200])
            return ToolResult(text or "(no resources)", f"{len(items)} resource(s)")

        bundle.tools["mcp_resources"] = ToolDef(
            "mcp_resources", "List the resources of an MCP server (no uri) or read one (uri).",
            {"type": "object", "properties": {"server": {"type": "string", "enum": with_resources}, "uri": {"type": "string"}}, "required": ["server"]},
            t_resources, "mcp", meta={"mcp": True, "readOnly": True})

    bundle.system = ("\n\n# MCP\n" + header + " Prefer them over guessing when they give authoritative data (docs, tickets, databases)."
                     + (" Read-only mode: only read-only tools are available." if read_only else "") + "\n" + "\n".join(lines))
    return bundle


def tool_read_only(tool: ToolDef, args: dict) -> bool:
    ro = tool.meta.get("readOnly")
    if isinstance(ro, dict):  # mcp_call: depends on the target tool
        return bool(ro.get((str(args.get("server") or ""), str(args.get("tool") or ""))))
    return bool(ro)


def tool_server(tool: ToolDef, args: dict) -> str:
    return str(tool.meta.get("server") or args.get("server") or "")


def _announce_project_servers(session, cfg: dict, notify) -> None:
    """Tell the user once that the project declares servers waiting for approval."""
    pending = pending_project_servers(cfg, session.directory)
    if not pending:
        return
    seen = session.data.setdefault("mcpProjectAnnounced", [])
    fresh = [n for n in pending if n not in seen]
    if fresh:
        seen.extend(fresh)
        notify(f"This project's .mcp.json declares MCP server(s) {', '.join(fresh)}. They will not start until you approve them "
               "(Settings > MCP, or the MCP chip above the prompt).")


def pending_project_servers(cfg: dict, directory: str) -> list[str]:
    from pathlib import Path

    approved = set(cfg["mcp"].get("approvedProjectServers") or [])
    try:
        root = str(Path(directory).resolve())
    except OSError:
        return []
    return [n for n in project_servers(directory) if f"{root}::{n}" not in approved and n not in cfg["mcp"]["servers"]]


def parse_import(text: str) -> dict[str, dict]:
    """Servers from pasted JSON: Claude Desktop / Claude Code ({"mcpServers": {...}}), VS Code ({"servers": {...}}),
    a bare {name: cfg} map or a single server object with a "name"."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    if isinstance(data.get("mcpServers"), dict):
        data = data["mcpServers"]
    elif isinstance(data.get("servers"), dict):
        data = data["servers"]
    elif isinstance(data.get("mcp"), dict) and isinstance(data["mcp"].get("servers"), dict):
        data = data["mcp"]["servers"]
    elif "command" in data or "url" in data:
        data = {str(data.get("name") or "server"): data}
    out = {}
    for name, cfg in data.items():
        if not isinstance(cfg, dict) or not (cfg.get("command") or cfg.get("url")):
            continue
        srv = {k: v for k, v in cfg.items() if k in ("type", "command", "args", "env", "cwd", "url", "headers", "timeout", "description")}
        if srv.get("type") in ("streamable-http", "streamableHttp", "http"):
            srv["type"] = "http"
        elif srv.get("type") not in ("stdio", "sse"):
            srv.pop("type", None)
        out[re.sub(r"[^A-Za-z0-9_.-]", "-", str(name))[:40]] = srv
    if not out:
        raise ValueError("No server found (each needs a command or a url)")
    return out


# Ready-made servers offered in Settings > MCP (all optional; npx/uvx need Node / uv installed).
PRESETS: list[dict] = [
    {"name": "context7", "title": "Context7 - up-to-date library docs", "type": "http", "url": "https://mcp.context7.com/mcp",
     "description": "Current documentation and code examples for thousands of libraries.", "readOnlyHint": True},
    {"name": "filesystem", "title": "Filesystem (extra folders)", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "<folder>"],
     "description": "Read and write files in folders outside the workspace."},
    {"name": "git", "title": "Git", "command": "uvx", "args": ["mcp-server-git"], "description": "Inspect git history, diffs and branches."},
    {"name": "fetch", "title": "Fetch (web pages as Markdown)", "command": "uvx", "args": ["mcp-server-fetch"],
     "description": "Fetch URLs and convert them to Markdown."},
    {"name": "memory", "title": "Memory (knowledge graph)", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
     "description": "Persistent memory across sessions as a local knowledge graph."},
    {"name": "sequential-thinking", "title": "Sequential thinking", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
     "description": "Structured step-by-step reasoning tool for hard problems."},
    {"name": "playwright", "title": "Playwright browser", "command": "npx", "args": ["-y", "@playwright/mcp@latest"],
     "description": "Drive a real browser: navigate, click, screenshot, test web UIs."},
    {"name": "github", "title": "GitHub", "type": "http", "url": "https://api.githubcopilot.com/mcp/",
     "headers": {"Authorization": "Bearer <token>"}, "description": "Issues, pull requests and code on GitHub."},
    {"name": "atlassian", "title": "Atlassian (Jira / Confluence)", "type": "http", "url": "https://mcp.atlassian.com/v1/mcp",
     "description": "Jira issues and Confluence pages."},
    {"name": "postgres", "title": "PostgreSQL (read-only)", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/db"],
     "description": "Inspect schemas and run read-only SQL."},
]


def rea_server(rev: dict) -> dict:
    """The REA MCP server entry for the reverse-engineering layer."""
    version = str(rev.get("version") or "6.4.0").strip() or "6.4.0"
    env = {**(rev.get("env") or {})}
    if rev.get("provider") and rev["provider"] != "auto":
        env["REA_ANALYSIS_PROVIDER"] = rev["provider"]
    env.setdefault("REA_MCP_INPUT_SCHEMA_PROFILE", rev.get("schemaProfile") or "compact")
    return {"type": "stdio", "command": "npx", "args": ["-y", f"rea-agents@{version}", "mcp"], "env": env, "enabled": True,
            "permission": "ask", "trustReadOnly": True, "timeout": int(rev.get("timeout") or 900), "startupTimeout": 300,
            "description": "REA - Reverse Engineer Anything (lawful use only)."}


def server_for_display(name: str, srv: dict, live: Optional[dict]) -> dict:
    return {"name": name, **srv, "live": live}
