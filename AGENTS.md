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
- UI colors come from CSS variables in `frontend/src/index.css` (`--x` hex for inline styles, `--x-rgb` channels for Tailwind `/opacity`). The UI is English only: all strings go through `i18n.ts`.
- Windows matters: read/write files with `osutil.read_text/write_text` (byte-exact newlines), show paths with `osutil.posix`, run processes with `osutil.run_command` (never asyncio subprocesses or `os.killpg`). CI runs on windows-latest.
- Auto mode (`config.auto_mode(cfg, session.data)`) must never leave a human prompt on the path: approvals, permissions and questions all have an auto branch.
- `scripts/fake_llm.py` is used by the tests: keep its role detection in sync with prompt headings in `backend/prompts.py` ("Phase: PLAN", "phase REVIEW", "phase CLOSE", "worker in the savvy-flow pipeline").

See `README.md` (features, architecture) and `filemap.md` (where things live).
