"""What MCP servers, skills and mods add to one agent run: extra tools and system-prompt sections."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import mcp_tools, mods, skills
from .config import load_config
from .tools import ToolDef


@dataclass
class Extras:
    tools: dict[str, ToolDef] = field(default_factory=dict)
    system: str = ""


async def prepare(session, spec, ctx, notify) -> Extras:
    """``spec.extensions``: "all" (build agent, workers), "local" (skills + mods, no MCP),
    "read" (plan agent, orchestrator: read-only MCP tools, skills, mod prompt sections) or "none"."""
    out = Extras()
    level = getattr(spec, "extensions", "none")
    if level == "none" or not spec.tools:
        return out
    read_only = level == "read"
    cfg = load_config()

    # Skills: names + descriptions in the prompt, bodies on demand (progressive disclosure).
    if cfg["skills"].get("modelInvocation", True):
        active = skills.active(session.directory, cfg)

        def on_load(name: str) -> None:
            used = session.data.setdefault("skillsUsed", [])
            if name not in used:
                used.append(name)

        tool = skills.tool_def(active, on_load)
        if tool:
            out.tools[tool.name] = tool
            out.system += skills.system_section(active)

    # MCP servers.
    if level == "all" or (read_only and cfg["mcp"].get("inPlanMode", True)):
        try:
            bundle = await mcp_tools.prepare(session, read_only, notify)
            out.tools.update(bundle.tools)
            out.system += bundle.system
        except Exception as e:  # noqa: BLE001 - MCP must never break a turn
            notify(f"MCP unavailable for this turn: {e}")

    # Mods: prompt sections, and their tools for agents that may act.
    out.system += await mods.compose(session, spec.name, read_only)
    if not read_only:
        out.tools.update(mods.agent_tools(session))
    return out
