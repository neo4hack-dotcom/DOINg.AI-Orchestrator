"""Mods: extend the agent with Python hooks, modelled on Claude Code mods.

A mod is a folder with ``mod.json`` (name, version, description, options) and ``mod.py``
exposing ``register(on, mod)``::

    def register(on, mod):
        @on("tool.call", {"tool": "bash"})
        async def guard($, e, next):
            if "rm -rf /" in e["input"].get("command", ""):
                return {"deny": "dangerous command"}
            return await next(e)

Every hook is ``(ctx, e, next)``: ``ctx`` is the mod API (UI, state, shell, LLM...),
``e`` the event, ``next(e)`` runs the hooks beneath and then the app's own behaviour and
returns the event's result. A hook that returns without calling ``next`` answers for
itself (e.g. ``{"deny": reason}``); ``next({**e, ...})`` rewrites what the rest sees.
Two-argument hooks ``(ctx, e)`` are observers: the chain continues automatically.

Events and results:
- ``session.start``  {session}                       -> None
- ``prompt.submit``  {text, agent}                   -> the event (possibly rewritten) or {deny}
- ``prompt.compose`` {agent, readOnly, sections}     -> {sections: [{id, text}]}  (system prompt)
- ``tool.call``      {tool, input, agent, runId}     -> {ok, output, title, metadata} or {deny}
- ``turn.complete``  {agent, text, files, runId}     -> None
- ``flow.phase``     {phase, title, flowId}          -> None
- ``command.run``    {name, args}                    -> {text} (shown) or {prompt} (sent to the agent)

Registration helpers: ``mod.command(name, description)``, ``mod.tool(name, description,
parameters, fn)``, ``mod.option(key, default, description)``.

Mods are loaded from ``backend/builtin/mods`` (shipped) and ``data/mods`` (yours). A
failing hook is isolated: the error is logged on the mod and the chain continues.
"""
from __future__ import annotations

import asyncio
import fnmatch
import importlib.util
import inspect
import json
import re
import shutil
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .config import DATA_DIR, load_config
from .osutil import atomic_write, read_text
from .osutil import run_command as shell_command

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin" / "mods"
USER_DIR = DATA_DIR / "mods"
STORE_DIR = DATA_DIR / "mods-store"
EVENTS = ("session.start", "prompt.submit", "prompt.compose", "tool.call", "turn.complete", "flow.phase", "command.run")
OBSERVER_TIMEOUT = 60.0
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


@dataclass
class HookDef:
    mod: str
    event: str
    matcher: dict
    fn: Callable
    observer: bool = False


@dataclass
class ModCommand:
    mod: str
    name: str
    description: str
    argument_hint: str = ""


@dataclass
class ModTool:
    mod: str
    name: str
    description: str
    parameters: dict
    fn: Callable
    permission: Optional[str] = None


@dataclass
class Mod:
    name: str
    path: Path
    scope: str
    manifest: dict = field(default_factory=dict)
    hooks: list[HookDef] = field(default_factory=list)
    commands: dict[str, ModCommand] = field(default_factory=dict)
    tools: dict[str, ModTool] = field(default_factory=dict)
    option_defaults: dict = field(default_factory=dict)
    option_docs: dict = field(default_factory=dict)
    error: str = ""
    logs: deque = field(default_factory=lambda: deque(maxlen=80))
    errors: int = 0

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "0.1.0")

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "")

    @property
    def default_enabled(self) -> bool:
        return self.manifest.get("enabledByDefault", True) is not False

    def log(self, text: str, level: str = "info") -> None:
        self.logs.append({"t": int(time.time() * 1000), "level": level, "text": str(text)[:2000]})

    def record_error(self, where: str, exc: BaseException) -> None:
        self.errors += 1
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-1500:]
        self.log(f"{where}: {type(exc).__name__}: {exc}\n{tb}", "error")


def _matches(matcher: dict, e: dict) -> bool:
    for key, want in (matcher or {}).items():
        value = str(e.get(key, ""))
        options = want if isinstance(want, (list, tuple, set)) else [want]
        if not any(fnmatch.fnmatchcase(value, str(o)) for o in options):
            return False
    return True


class Registrar:
    """The ``mod`` object given to ``register(on, mod)``."""

    def __init__(self, mod: Mod):
        self._mod = mod
        self.name = mod.name

    def command(self, name: str, description: str = "", argument_hint: str = "") -> None:
        name = name.strip().lstrip("/")
        self._mod.commands[name] = ModCommand(self._mod.name, name, description, argument_hint)

    def tool(self, name: str, description: str, parameters: Optional[dict] = None, fn: Optional[Callable] = None,
             permission: Optional[str] = None):
        def deco(f: Callable) -> Callable:
            self._mod.tools[name] = ModTool(self._mod.name, name, description,
                                            parameters or {"type": "object", "properties": {}}, f, permission)
            return f
        return deco(fn) if fn else deco

    def option(self, key: str, default: Any = None, description: str = "") -> None:
        self._mod.option_defaults.setdefault(key, default)
        if description:
            self._mod.option_docs[key] = description

    def log(self, text: str) -> None:
        self._mod.log(text)


# ---------------------------------------------------------------------------
# registry


class ModRegistry:
    def __init__(self) -> None:
        self.mods: dict[str, Mod] = {}
        self.loaded = False
        self.generation = 0
        self.listeners: list[Callable[[], None]] = []

    def folders(self) -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        for scope, root in (("user", USER_DIR), ("builtin", BUILTIN_DIR)):
            if root.is_dir():
                out += [(scope, p) for p in sorted(root.iterdir()) if p.is_dir() and (p / "mod.json").is_file()]
        return out

    def load_all(self) -> None:
        self.generation += 1
        mods: dict[str, Mod] = {}
        for scope, folder in self.folders():
            mod = self._load(scope, folder)
            if mod.name not in mods:  # a user mod shadows a builtin one of the same name
                mods[mod.name] = mod
        self.mods = mods
        self.loaded = True
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def _load(self, scope: str, folder: Path) -> Mod:
        try:
            manifest = json.loads(read_text(folder / "mod.json"))
        except (OSError, json.JSONDecodeError) as e:
            return Mod(folder.name, folder, scope, error=f"mod.json: {e}")
        name = str(manifest.get("name") or folder.name)
        mod = Mod(name, folder, scope, manifest)
        for key, spec in (manifest.get("options") or {}).items():
            if isinstance(spec, dict):
                mod.option_defaults[key] = spec.get("default")
                mod.option_docs[key] = str(spec.get("description") or "")
            else:
                mod.option_defaults[key] = spec
        entry = folder / str(manifest.get("main") or "mod.py")
        if not entry.is_file():
            mod.error = f"{entry.name} not found"
            return mod
        module_name = f"supercode_mod_{re.sub(r'[^a-zA-Z0-9_]', '_', name)}_{self.generation}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError("mod.py must define register(on, mod)")

            def on(event: str, matcher: Any = None, hook: Optional[Callable] = None):
                if callable(matcher) and hook is None:
                    matcher, hook = None, matcher
                if event not in EVENTS:
                    raise ValueError(f"unknown event '{event}' (known: {', '.join(EVENTS)})")

                def deco(fn: Callable) -> Callable:
                    try:
                        observer = len(inspect.signature(fn).parameters) < 3
                    except (TypeError, ValueError):
                        observer = False
                    mod.hooks.append(HookDef(name, event, dict(matcher or {}), fn, observer))
                    return fn
                return deco(hook) if hook else deco

            register(on, Registrar(mod))
        except Exception as e:  # noqa: BLE001 - a broken mod must not break the app
            mod.error = f"{type(e).__name__}: {e}"
            mod.record_error("load", e)
            mod.hooks, mod.commands, mod.tools = [], {}, {}
        return mod

    def ensure(self) -> None:
        if not self.loaded:
            self.load_all()

    def enabled(self, mod: Mod, cfg: Optional[dict] = None) -> bool:
        cfg = cfg or load_config()
        mc = cfg.get("mods") or {}
        if mod.error:
            return False
        if mod.name in (mc.get("disabled") or []):
            return False
        return mod.default_enabled or mod.name in (mc.get("enabled") or [])

    def active(self) -> list[Mod]:
        self.ensure()
        cfg = load_config()
        return [m for m in self.mods.values() if self.enabled(m, cfg)]

    def hooks(self, event: str, e: dict) -> list[HookDef]:
        return [h for m in self.active() for h in m.hooks if h.event == event and _matches(h.matcher, e)]

    def commands(self) -> dict[str, ModCommand]:
        out: dict[str, ModCommand] = {}
        for m in self.active():
            for name, c in m.commands.items():
                out.setdefault(name, c)
        return out

    def tools(self) -> dict[str, ModTool]:
        out: dict[str, ModTool] = {}
        for m in self.active():
            for name, t in m.tools.items():
                out.setdefault(name, t)
        return out

    def options(self, mod: Mod) -> dict:
        stored = ((load_config().get("mods") or {}).get("options") or {}).get(mod.name) or {}
        return {**mod.option_defaults, **stored}

    def listing(self) -> list[dict]:
        self.ensure()
        cfg = load_config()
        out = []
        for m in self.mods.values():
            out.append({
                "name": m.name, "version": m.version, "description": m.description, "scope": m.scope, "path": str(m.path),
                "enabled": self.enabled(m, cfg), "defaultEnabled": m.default_enabled, "error": m.error, "errors": m.errors,
                "events": sorted({h.event + (f" {json.dumps(h.matcher)}" if h.matcher else "") for h in m.hooks}),
                "commands": [{"name": c.name, "description": c.description, "argumentHint": c.argument_hint} for c in m.commands.values()],
                "tools": [{"name": t.name, "description": t.description} for t in m.tools.values()],
                "options": self.options(m), "optionDocs": m.option_docs, "logs": list(m.logs)[-30:],
                "readOnly": m.scope == "builtin", "homepage": m.manifest.get("homepage", ""),
                "inspiredBy": m.manifest.get("inspiredBy", ""),
            })
        return out

    def validate(self, folder: Path) -> dict:
        """Load a mod folder in isolation and report what it registers (or why it fails)."""
        if not (folder / "mod.json").is_file():
            return {"ok": False, "error": "mod.json is missing"}
        self.generation += 1
        mod = self._load("user", folder)
        return {"ok": not mod.error, "error": mod.error, "name": mod.name,
                "events": [f"{h.event} {json.dumps(h.matcher) if h.matcher else ''}".strip() for h in mod.hooks],
                "commands": list(mod.commands), "tools": list(mod.tools), "options": mod.option_defaults,
                "log": [l["text"] for l in mod.logs][-5:]}


registry = ModRegistry()


# ---------------------------------------------------------------------------
# the API a hook receives


class ModContext:
    def __init__(self, mod: Mod, session: Any, tool_ctx: Any = None):
        self._mod = mod
        self._session = session
        self._tool_ctx = tool_ctx
        self.name = mod.name
        self.options = registry.options(mod)
        self._store: Optional[dict] = None
        self._store_snapshot = ""
        if session is not None:
            self.state = session.data.setdefault("modState", {}).setdefault(mod.name, {})
        else:
            self.state = {}
        self._state_snapshot = json.dumps(self.state, sort_keys=True, default=str)

    # -- facts -------------------------------------------------------------
    @property
    def directory(self) -> str:
        return self._session.directory if self._session is not None else str(Path.cwd())

    @property
    def session(self) -> dict:
        s = self._session
        if s is None:
            return {}
        cfg = load_config()
        d = s.data
        return {"id": s.id, "title": d.get("title"), "directory": s.directory, "agent": d.get("agent"),
                "model": cfg["llm"].get("model"), "provider": cfg["llm"].get("provider"),
                "contextTokens": d.get("contextTokens", 0), "contextWindow": int(cfg["llm"].get("contextWindow") or 16384),
                "tokens": dict(d.get("tokens") or {}), "autoMode": d.get("autoMode"), "createdAt": d.get("createdAt"),
                "flow": (d.get("flow") or {}).get("phase"), "busy": s.busy}

    @property
    def store(self) -> dict:
        if self._store is None:
            p = STORE_DIR / f"{self.name}.json"
            try:
                self._store = json.loads(read_text(p)) if p.is_file() else {}
            except (OSError, json.JSONDecodeError):
                self._store = {}
            self._store_snapshot = json.dumps(self._store, sort_keys=True, default=str)
        return self._store

    # -- UI ----------------------------------------------------------------
    def _ui(self) -> dict:
        return self._session.data.setdefault("modUi", {"status": {}, "band": {}, "panels": {}})

    def _emit_ui(self) -> None:
        if self._session is not None:
            self._session.emit("mods.ui", {"modUi": self._ui()})
            self._session.touch()

    def status(self, text: Optional[str]) -> None:
        """Set (or clear with None) this mod's entry in the status line."""
        if self._session is None:
            return
        ui = self._ui()["status"]
        if text:
            ui[self.name] = str(text)[:200]
        else:
            ui.pop(self.name, None)
        self._emit_ui()

    def band(self, markdown: Optional[str], actions: Optional[list[dict]] = None) -> None:
        """A band above the prompt; ``actions`` = [{label, command}] buttons running a slash command."""
        if self._session is None:
            return
        ui = self._ui()["band"]
        if markdown:
            ui[self.name] = {"markdown": str(markdown)[:2000],
                             "actions": [{"label": str(a.get("label"))[:40], "command": str(a.get("command"))[:200]}
                                         for a in (actions or []) if a.get("label") and a.get("command")][:4]}
        else:
            ui.pop(self.name, None)
        self._emit_ui()

    def panel(self, markdown: Optional[str], title: Optional[str] = None) -> None:
        """This mod's section of the Mods tab in the side panel."""
        if self._session is None:
            return
        ui = self._ui()["panels"]
        if markdown:
            ui[self.name] = {"title": title or self.name, "markdown": str(markdown)[:20000]}
        else:
            ui.pop(self.name, None)
        self._emit_ui()

    def toast(self, text: str, kind: str = "info") -> None:
        if self._session is not None:
            self._session.emit("mods.toast", {"mod": self.name, "text": str(text)[:300], "kind": kind})

    # -- capabilities --------------------------------------------------------
    async def run(self, command: str, timeout: int = 60, cwd: Optional[str] = None) -> tuple[int, str]:
        code, out, _timed_out = await shell_command(command, cwd or self.directory, timeout)
        return code, out

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else Path(self.directory) / p

    def read(self, rel: str) -> str:
        return read_text(self.path(rel))

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def log(self, text: str, level: str = "info") -> None:
        self._mod.log(text, level)

    def journal(self, kind: str = "mod", **data: Any) -> None:
        if self._session is not None:
            from . import journal

            journal.log(self._session, "mod", mod=self.name, event=kind, **data)

    async def llm(self, prompt: str, system: str = "", json_mode: bool = False, temperature: float = 0.2) -> str:
        from . import llm

        cfg = load_config()
        res = await llm.chat(cfg["llm"], model=cfg["llm"]["model"], temperature=temperature, json_mode=json_mode,
                             messages=([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}])
        if self._session is not None:
            tot = self._session.data.setdefault("tokens", {"input": 0, "output": 0})
            tot["input"] += res.prompt_tokens
            tot["output"] += res.completion_tokens
        return res.text

    def flush(self) -> None:
        if self._session is not None and json.dumps(self.state, sort_keys=True, default=str) != self._state_snapshot:
            self._session.touch()
        if self._store is not None and json.dumps(self._store, sort_keys=True, default=str) != self._store_snapshot:
            STORE_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write(STORE_DIR / f"{self.name}.json", json.dumps(self._store, indent=1, default=str))


# ---------------------------------------------------------------------------
# dispatch


async def dispatch(event: str, session: Any, e: dict, default: Callable[[dict], Awaitable[Any]], tool_ctx: Any = None) -> Any:
    """Run the hooks registered for ``event`` around ``default`` (the app's own behaviour)."""
    chain = registry.hooks(event, e)
    if not chain:
        return await default(e)

    async def step(i: int, ev: dict) -> Any:
        if i >= len(chain):
            return await default(ev)
        h = chain[i]
        mod = registry.mods.get(h.mod)
        if mod is None:
            return await step(i + 1, ev)
        ctx = ModContext(mod, session, tool_ctx)
        st: dict = {"called": False, "result": None}

        async def nxt(ev2: Optional[dict] = None) -> Any:
            if st["called"]:
                return st["result"]
            st["called"] = True
            st["result"] = await step(i + 1, ev if ev2 is None else ev2)
            return st["result"]

        try:
            out = h.fn(ctx, ev) if h.observer else h.fn(ctx, ev, nxt)
            if inspect.isawaitable(out):
                out = await out
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate the failing mod
            mod.record_error(event, exc)
            if session is not None:
                session.emit("mods.toast", {"mod": mod.name, "text": f"{mod.name} failed on {event}: {exc}"[:300], "kind": "error"})
            return st["result"] if st["called"] else await step(i + 1, ev)
        finally:
            ctx.flush()
        if h.observer or out is None:
            return st["result"] if st["called"] else await nxt()
        if isinstance(out, dict) and "deny" in out and not st["called"]:
            out.setdefault("mod", mod.name)
            mod.log(f"{event} denied: {out['deny']}")
        return out

    return await step(0, e)


async def notify(event: str, session: Any, e: dict) -> None:
    """Fire an observer-style event (no result); never raises, bounded in time."""
    async def nothing(_e: dict) -> None:
        return None

    try:
        await asyncio.wait_for(dispatch(event, session, e, nothing), OBSERVER_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        pass


async def compose(session: Any, agent: str, read_only: bool) -> str:
    """System prompt sections contributed by mods."""
    async def base(ev: dict) -> dict:
        return {"sections": list(ev.get("sections") or [])}

    try:
        res = await asyncio.wait_for(dispatch("prompt.compose", session, {"agent": agent, "readOnly": read_only, "sections": []}, base),
                                     OBSERVER_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        return ""
    sections = [s for s in (res or {}).get("sections") or [] if isinstance(s, dict) and s.get("text")]
    return "".join(f"\n\n{str(s['text']).strip()[:4000]}" for s in sections)


async def run_command(session: Any, name: str, args: str) -> Optional[dict]:
    if name not in registry.commands():
        return None

    async def unknown(_e: dict) -> dict:
        return {"text": f"/{name}: no mod answered."}

    res = await dispatch("command.run", session, {"name": name, "args": args}, unknown)
    return res if isinstance(res, dict) else {"text": str(res)}


def agent_tools(session: Any):
    """ToolDefs for the tools mods registered (prefixed when they clash with a builtin)."""
    from .tools import TOOLS, ToolContext, ToolDef, ToolError, ToolResult

    out = {}
    for name, t in registry.tools().items():
        tool_name = name if name not in TOOLS else f"mod_{name}"

        async def run(ctx: ToolContext, args: dict, _t: ModTool = t) -> ToolResult:
            mod = registry.mods.get(_t.mod)
            if mod is None:
                raise ToolError("mod unloaded")
            mctx = ModContext(mod, session, ctx)
            try:
                res = _t.fn(mctx, args)
                if inspect.isawaitable(res):
                    res = await res
            except ToolError:
                raise
            except Exception as e:  # noqa: BLE001
                mod.record_error(f"tool {_t.name}", e)
                raise ToolError(f"{_t.mod}/{_t.name}: {type(e).__name__}: {e}") from e
            finally:
                mctx.flush()
            if isinstance(res, dict):
                return ToolResult(str(res.get("output", "")), str(res.get("title", _t.name)), dict(res.get("metadata") or {}))
            return ToolResult(str(res), _t.name)

        out[tool_name] = ToolDef(tool_name, f"[mod {t.mod}] {t.description}", t.parameters, run, t.permission, meta={"mod": t.mod})
    return out


# ---------------------------------------------------------------------------
# creating mods (the /mod flow and the Mods manager)


def user_folder(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("Mod names: lowercase letters, digits and hyphens (max 48)")
    return USER_DIR / name


def save_files(name: str, files: dict[str, str]) -> Path:
    folder = user_folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        rel = rel.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise ValueError(f"invalid path {rel}")
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, text)
    return folder


def delete(name: str) -> None:
    folder = user_folder(name)
    if not folder.is_dir():
        raise FileNotFoundError(name)
    shutil.rmtree(folder)


def source(name: str) -> dict[str, str]:
    registry.ensure()
    mod = registry.mods.get(name)
    if not mod:
        raise FileNotFoundError(name)
    files = {}
    for p in sorted(mod.path.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml") and "__pycache__" not in p.parts:
            try:
                files[p.relative_to(mod.path).as_posix()] = read_text(p)
            except (OSError, UnicodeDecodeError):
                continue
    return files


TEMPLATE_JSON = """{
  "name": "%(name)s",
  "version": "0.1.0",
  "description": "%(description)s",
  "enabledByDefault": true,
  "options": {}
}
"""

TEMPLATE_PY = '''"""%(description)s"""


def register(on, mod):
    @on("turn.complete")
    async def done(ctx, e):
        ctx.status(f"{len(e.get('files') or [])} file(s) changed")
'''
