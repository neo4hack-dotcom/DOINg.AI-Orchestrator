# AGENTS.md

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
- savvy-flow sequencing lives in `backend/flow.py`, not in prompts: prompts only ask for one step with a strict output format (local models).
- UI colors come from CSS variables in `frontend/src/index.css` (`--x` hex for inline styles, `--x-rgb` channels for Tailwind `/opacity`). All UI strings go through `i18n.ts` (fr + en).
- `scripts/fake_llm.py` is used by the tests: keep its role detection in sync with prompt headings in `backend/prompts.py` ("Phase: PLAN", "phase REVIEW", "phase CLOSE", "worker in the savvy-flow pipeline").

See `README.md` (features, architecture) and `filemap.md` (where things live).
