# AGENTS.md — DOINg.AI SuperCode

Stack: Python 3.10+ FastAPI backend (`backend/`) + React 19 / Vite 6 / Tailwind 3.4 / TypeScript frontend (`frontend/`).

Commands:
- Backend tests: `python -m pytest`
- Frontend typecheck: `cd frontend && npm run lint`; build: `npm run build` (output `frontend/dist`, served by FastAPI)
- Run: `python server.py` (API + built UI on :4001); dev UI: `cd frontend && npm run dev` (:4000, proxies /api)
- Demo LLM: `python scripts/fake_llm.py --port 11500`

Conventions:
- Backend state the UI shows is plain dicts on `Session.data`; every change goes through `MessageSink` / `Session.emit` so SSE clients stay in sync. Add new event types to `frontend/src/types.ts` and `sessionReducer.ts`.
- Internal LLM messages use one shape (see `backend/llm.py` docstring); providers convert. Never pass provider-specific shapes around.
- Tools must resolve paths with `tools.resolve()` (workspace sandbox) and record edits via `_record_change` (feeds the savvy-flow review diff).
- savvy-flow sequencing lives in `backend/flow.py`, not in prompts: prompts only ask for one step with a strict output format (local models). Graph rules (deps, conflicts, waves) live in `backend/planner.py` and are unit-tested.
- Session state the UI needs must stay JSON-serializable; worker transcripts are stored per run (`data/sessions/<id>/<run>.json`) and served by `/runs/{id}`. Every event carries `seq`: the client drops events older than its snapshot.
- Anything worth auditing goes through `journal.log(session, kind, ...)`.
- UI colors come from CSS variables in `frontend/src/index.css` (`--x` hex for inline styles, `--x-rgb` channels for Tailwind `/opacity`). The UI is English only: shared strings go through `i18n.ts`; the self-contained panels (Activity view, Settings > MCP / Skills / Mods / Reverse eng.) keep their English copy inline.
- Windows matters: read/write files with `osutil.read_text/write_text` (byte-exact newlines), show paths with `osutil.posix`, run processes with `osutil.run_command` (never asyncio subprocesses or `os.killpg`). CI runs on windows-latest.
- Permission modes (`config.permission_mode(cfg, session.data)`: `ask` / `acceptEdits` / `auto`) are decided in `agent.decide`; `config.auto_mode` is `mode == "auto"`. Auto must never leave a human prompt on the path: approvals, permissions and questions all have an auto branch. A tool that acts outside the project (system app, network, database write) needs its own permission category.
- Privacy: every outbound HTTP request goes through `netguard.client()` (never a bare `httpx.AsyncClient`), and new destinations belong in `netguard.describe` (Settings > Privacy & security). Shell commands that reach the network are classified in `permissions.network_kind`. Keep `SECURITY.md` in sync.
- Processes for the terminal, `!` shell and background `bash` go through `processes.manager` (live `terminal.*` events); the Data add-in keeps whole results in `data/datasets/` (index in `session.data["datasets"]`) and only sends a few rows to the LLM.
- `scripts/fake_llm.py` is used by the tests: keep its role detection in sync with prompt headings in `backend/prompts.py` ("Phase: PLAN", "phase REVIEW", "phase CLOSE", "worker in the savvy-flow pipeline").
- Extensions: an `AgentSpec.extensions` level (`all` / `local` / `read` / `none`) decides what MCP tools, skills and mod tools an agent gets (`backend/extensions.py`). Every tool call goes through the mods' `tool.call` chain in `agent.execute_tool`: keep permission + execution inside the chain's default (`engine`). New tool metadata worth reporting goes in `ToolResult.metadata` (edit tools set `files`).
- MCP: never import an SDK at runtime - `backend/mcp_client.py` speaks the protocol; test interop in `backend/tests/test_mcp.py`. A project's `.mcp.json` server must stay inert until approved.
- Built-in mods and skills live in `backend/builtin/`; mods use only the stdlib and the `ctx` API (they also run on Windows: quote paths, no bash-only syntax). The REA skill is vendored with its instructions unchanged (only the frontmatter and a short header note were added; MIT: keep LICENSE and DISCLAIMER.md).

See `README.md` (features, architecture) and `filemap.md` (where things live).
