# filemap

server.py — entry point: runs uvicorn on backend.main:app (:4001), --workspace, --open
requirements.txt — Python deps (FastAPI, uvicorn, httpx, pytest)
install.bat / install.sh — venv + pip + npm install + frontend build
start.bat / start.sh / start-dev.bat — launch prod / dev
pytest.ini — test config (asyncio auto mode)

backend/ — Python API
backend/main.py — FastAPI routes (config, sessions, chat, SSE, permissions, flow approve, files), static UI, NEXUS.AI first-run import
backend/config.py — data/config.json: llm block (NEXUS.AI-compatible), tiers, permissions, flow options; NEXUS.AI db.json import
backend/llm.py — providers (Ollama, OpenAI-compatible, n8n), streaming, native tools, <tool_call> text protocol, <think> split
backend/agent.py — agent loop shared by every role; tool execution and permission prompts
backend/tools.py — tool implementations + registry (read, list, glob, grep, write, edit, bash, todo, step, task)
backend/flow.py — savvy-flow engine (plan, design, delegate, review, close) and run_worker
backend/prompts.py — system prompts: base, build, plan, orchestrator phases, workers per tier
backend/session.py — Session, MessageSink (transcript writer + events), SessionStore (data/sessions/*.json)
backend/tests/ — pytest suite (uses scripts/fake_llm.py over real HTTP)

frontend/ — React UI (Vite)
frontend/src/App.tsx — layout, session loading + SSE, commands, panels
frontend/src/api.ts — REST + EventSource client
frontend/src/types.ts — mirrors backend JSON shapes and events
frontend/src/sessionReducer.ts — applies SSE events to the open session
frontend/src/i18n.ts — fr / en strings
frontend/src/util.ts — formatting helpers, tier list, agent colors
frontend/src/index.css — theme variables, markdown, animations (tier crew, shimmer)
frontend/src/components/Sidebar.tsx — sessions list, LLM status, settings entry
frontend/src/components/Transcript.tsx — message/part rendering
frontend/src/components/ToolPartView.tsx — one tool call (status, diff, output)
frontend/src/components/PlanCard.tsx — savvy-flow plan + approve
frontend/src/components/ReviewCard.tsx — orchestrator review verdict
frontend/src/components/ProgressBar.tsx — savvy-progress bar
frontend/src/components/AgentsPanel.tsx — savvy-progress agents panel (runs, planned tasks, transcripts)
frontend/src/components/TierIcon.tsx — tier crew icons + chips
frontend/src/components/Composer.tsx — prompt, agent switch, / commands, @ files
frontend/src/components/PermissionBar.tsx — pending permission prompt
frontend/src/components/TodoList.tsx — session todos
frontend/src/components/SettingsModal.tsx — LLM, tiers, workspace/permissions, flow settings
frontend/src/components/Markdown.tsx, DiffView.tsx — renderers

scripts/fake_llm.py — scripted fake LLM (Ollama + OpenAI wire formats) for demos and tests
data/ — runtime config and sessions (git-ignored)
