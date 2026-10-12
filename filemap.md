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
backend/agent.py — agent loop shared by every role: retries, pruning/compaction, loop guard, permissions, questions, journal
backend/compaction.py — prune old tool outputs, structured checkpoint summaries
backend/editing.py — tolerant edit matching (exact/normalized/whitespace/indentation), apply_patch parser
backend/permissions.py — bash pattern rules, compound commands, "always" grant patterns
backend/planner.py — plan decomposition: modules, deps, tier floor, merge/split helpers, file conflicts, waves, priorities
backend/repomap.py — ranked file/signature map (ast for Python, regex for JS/TS/Go/Rust/Java...)
backend/snapshot.py — shadow git snapshots: track, changed, diff, restore (undo, revert file)
backend/journal.py — JSONL journal per session + Markdown report
backend/tools.py — tool implementations + registry (read, list, glob, grep, repomap, write, edit, multiedit, apply_patch, bash, webfetch, todo, question, notes, step, task)
backend/flow.py — savvy-flow engine: architect + per-module planning, DAG scheduler, auto-verify/fix, review, gates, integration, close; run_worker
backend/prompts.py — system prompts: base, build, plan, orchestrator phases, workers per tier
backend/session.py — Session, MessageSink (transcript writer + events), SessionStore (data/sessions/*.json)
backend/tests/ — pytest suite (uses scripts/fake_llm.py over real HTTP): test_tools, test_harness, test_app, test_long_run

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
frontend/src/components/SidePanel.tsx — right panel tabs: Agents, Plan, Files, Trace
frontend/src/components/AgentsPanel.tsx — savvy-progress agents panel (runs, planned tasks, lazy-loaded transcripts)
frontend/src/components/PlanBoard.tsx — modules, tasks, waves, critical path, checks, notes
frontend/src/components/FilesPanel.tsx — files changed since session start, diff, revert
frontend/src/components/TracePanel.tsx — live journal with filters, report export
frontend/src/components/CommandPalette.tsx — Ctrl+K palette
frontend/src/components/QuestionBar.tsx — answers to the question tool
frontend/src/components/TierIcon.tsx — tier crew icons + chips
frontend/src/components/Composer.tsx — prompt, agent switch, / commands, @ files, queue, history, drafts, status bar
frontend/src/notify.ts — desktop notifications + title badge
frontend/src/components/PermissionBar.tsx — pending permission prompt
frontend/src/components/TodoList.tsx — session todos
frontend/src/components/SettingsModal.tsx — LLM, tiers, workspace/permissions, flow settings
frontend/src/components/Markdown.tsx, DiffView.tsx — renderers

scripts/fake_llm.py — scripted fake LLM (Ollama + OpenAI wire formats) for demos and tests
data/ — runtime config and sessions (git-ignored)
