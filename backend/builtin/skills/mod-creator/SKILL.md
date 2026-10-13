---
name: mod-creator
description: Write a DOINg.AI SuperCode mod (Python hooks on the agent's events - tool calls, prompts, turns, slash commands, status line, band above the prompt). Use when the user asks to create, change or debug a mod, a hook, a guard, a status line item or a custom slash command.
argument-hint: "<what the mod should do>"
user-invocable: false
---

# Mod creator

A mod is a folder `<mods dir>/<name>/` with two files. Mods run inside the SuperCode server process, on every session.

`mod.json`:
```json
{
  "name": "my-mod",
  "version": "0.1.0",
  "description": "One line: what it does.",
  "enabledByDefault": true,
  "options": {"threshold": {"default": 3, "description": "Shown in Settings > Mods"}}
}
```

`mod.py` exposes `register(on, mod)`:
```python
def register(on, mod):
    @on("tool.call", {"tool": "bash"})
    async def guard(ctx, e, next):
        if "rm -rf /" in e["input"].get("command", ""):
            return {"deny": "dangerous command"}          # the tool does not run; the agent is told why
        return await next(e)                               # run the rest of the chain and the tool
```

## Hooks
Every hook is `(ctx, e, next)` (sync or async). `next(e)` runs the hooks beneath, then the app's own behaviour, and
returns the event's result. Return without calling `next` to answer for yourself; call `next({**e, ...})` to rewrite
the event. A hook with only `(ctx, e)` is an **observer**: the chain continues by itself.
Matchers are dicts of glob patterns on event fields: `{"tool": ["edit", "write", "multiedit", "apply_patch"]}`.

| Event | `e` | Result of `next(e)` / what to return |
|---|---|---|
| `session.start` | `{session}` | nothing (observer) |
| `prompt.submit` | `{text, agent}` | the event (rewrite `text`) or `{"deny": reason}` |
| `prompt.compose` | `{agent, readOnly, sections}` | `{"sections": [{"id", "text"}]}` - append a section to add system-prompt text |
| `tool.call` | `{tool, input, agent, runId}` | `{"ok", "output", "title", "metadata"}` - post-process `output` (what the model sees); or return `{"deny": reason}` before calling `next` |
| `turn.complete` | `{agent, text, files, runId}` | nothing (observer); `files` = paths changed in the turn |
| `flow.phase` | `{phase, title, flowId}` | nothing (observer) - savvy-flow phases |
| `command.run` | `{name, args}` | `{"text": markdown}` (shown to the user) or `{"prompt": text}` (sent to the agent) |

Edit tools put the changed paths in `result["metadata"]["files"]`; bash results have `metadata["exitCode"]`.

## Registration helpers (in `register`)
- `mod.command("name", "description", argument_hint="")` - a slash command `/name args`, answered by an
  `on("command.run", {"name": "name"}, hook)` hook.
- `@mod.tool("name", "description", {"type": "object", "properties": {...}, "required": [...]})` decorating
  `async def fn(ctx, args) -> str` - a tool the agent can call.
- `mod.option("key", default, "description")` - user-editable option (also declarable in mod.json).

## The `ctx` API
- `ctx.status(text | None)` - this mod's item in the status line (None clears it).
- `ctx.band(markdown | None, actions=[{"label": "Commit", "command": "/commit"}])` - band above the prompt with buttons.
- `ctx.panel(markdown | None, title=None)` - this mod's section in the side panel's Mods tab.
- `ctx.toast(text, kind="info" | "success" | "warning" | "error")`.
- `ctx.state` - dict persisted with the session; `ctx.store` - dict persisted across sessions.
- `ctx.options` - the mod's options; `ctx.session` - {id, title, directory, agent, model, contextTokens,
  contextWindow, tokens, autoMode, flow, busy}; `ctx.directory`.
- `await ctx.exec(["git", "add", path], timeout=60)` -> `(exit_code, output)`: a program with an argument list, no shell.
  ALWAYS use it when a file name, branch name or any data goes into the command.
- `await ctx.run("git status --short", timeout=60)` -> the same through the shell (bash / PowerShell), for fixed command lines only.
- `ctx.read(path)`, `ctx.exists(path)`, `ctx.path(path)` (project-relative), `ctx.which("prettier")`.
- `await ctx.llm(prompt, system="", json_mode=False)` -> text from the configured local model.
- `ctx.log(text)` (Settings > Mods > logs), `ctx.journal(kind, **data)` (session journal / report).

## Rules
1. Stdlib only (plus the `ctx` API). Never block the event loop: use `await ctx.run(...)` for processes.
2. Keep hooks fast; cache expensive facts in `ctx.state` with a timestamp.
3. Always `return await next(e)` in a non-observer hook unless you deliberately answer for the event.
4. Fail soft: a hook exception is isolated and logged, but think about edge cases (missing keys, Windows paths).
5. Cross-platform: paths with `ctx.path()`, no shell-specific syntax unless guarded.

## Workflow (when building a mod for the user)
1. Choose a short lowercase-hyphenated name. 2. Write `mod.json` then `mod.py` in the mods folder.
3. Call `mod_validate` with the name and fix every error it reports.
4. Call `mod_install` with the name and a one-line summary: the user approves, the mod loads immediately.
5. Tell the user how to use it (command names, options) in two or three lines.
