# filemap

server.py — entry point: runs uvicorn on backend.main:app (127.0.0.1:4001), --host (token required beyond loopback), --token, --workspace, --open
SECURITY.md — data flows, outbound destinations, protections, residual risks
requirements.txt — Python deps (FastAPI, uvicorn, httpx, reportlab, pyyaml, pytest)
install.bat / install.sh — venv + pip + npm install + frontend build
start.bat / start.sh / start-dev.bat — launch prod / dev
.github/workflows/ci.yml — tests + build + smoke test on Windows, Ubuntu, macOS
.gitattributes — CRLF for .bat, LF for .sh
pytest.ini — test config (asyncio auto mode)

backend/ — Python API
backend/main.py — FastAPI routes (config, privacy report, sessions + permission mode, chat, SSE, permissions, flow approve, files, slash commands, MCP, skills, mods, reverse, activity/report), mod builder, static UI with CSP, NEXUS.AI first-run import
backend/security.py — HTTP guard middleware: Host allowlist (DNS rebinding), CSRF origin check, optional LAN token; UI and preview CSPs
backend/netguard.py — local-only mode: what counts as local, httpx client checking every request (redirects included), privacy destinations
backend/processes.py — ProcessManager: terminal / background / ! shell processes, live output events, stdin, kill, URL detection, terminal cwd
backend/workspace_api.py — routes: terminal, ! shell mode, file tree, read / write / delete files, mkdir, open with the system app, raw files (sandboxed), preview targets
backend/data.py — data add-in: sources (MCP SQL servers, SQLite, CSV / TSV / JSON / XLSX), SQL with write detection, result parsing, datasets, views (filters / sort / hidden / search), profile, aggregate, CSV / XLSX / JSON export, data_* agent tools
backend/data_api.py — data routes: sources, tables, columns, query (409 to confirm writes), open table / file, datasets, view, profile, aggregate, export
backend/config.py — data/config.json: llm block (NEXUS.AI-compatible), tiers, permission mode + permissions, network (local-only), data, terminal, flow options, mcp / skills / mods / reverse sections, secret masking; NEXUS.AI db.json import
backend/llm.py — providers (Ollama, OpenAI-compatible, n8n), streaming, native tools, <tool_call> text protocol, <think> split
backend/agent.py — agent loop shared by every role: retries, pruning/compaction, loop guard, permission decisions per mode (decide, settle_pending; incl. MCP, network commands), questions, journal, mod tool.call chain, timed reasoning parts
backend/extensions.py — what MCP servers, skills and mods add to one agent run (tools + system-prompt sections)
backend/mcp_client.py — MCP protocol client: stdio / Streamable HTTP / SSE transports, connection lifecycle, result formatting, McpManager
backend/mcp_tools.py — MCP tools for agents: namespaced tools, tool search (mcp_search / mcp_call), mcp_resources, permissions, project approval, presets, JSON import, REA server entry
backend/skills.py — Agent Skills: SKILL.md parsing, discovery (builtin / user / project), skill tool, /skill commands, editing, import, AI generation
backend/mods.py — mods: loader, hook chains (tool.call, prompt.*, turn.complete, flow.phase, command.run), ctx API (status, band, panel, toast, state, store, run, llm), mod tools
backend/report.py — activity collection for the UI + Markdown and reportlab PDF report
backend/builtin/skills/ — built-in skills (skill-creator, mod-creator, code-review, test-driven-development, systematic-debugging, reverse-engineer-anything from REA, MIT)
backend/builtin/mods/ — top 5 built-in mods: statusline, format-on-edit, safety-guard, diagnostics, commit-commands
backend/compaction.py — prune old tool outputs, structured checkpoint summaries
backend/editing.py — tolerant edit matching (exact/normalized/whitespace/indentation), apply_patch parser
backend/permissions.py — bash pattern rules, compound commands, "always" grant patterns, allow-rule hardening (substitutions, redirections, exec options), network / package command classification
backend/planner.py — plan decomposition: modules, deps, tier floor, merge/split helpers, file conflicts, waves, priorities
backend/repomap.py — ranked file/signature map (ast for Python, regex for JS/TS/Go/Rust/Java...)
backend/snapshot.py — shadow git snapshots: track, changed, diff, restore (undo, revert file)
backend/journal.py — JSONL journal per session + Markdown report
backend/osutil.py — cross-platform: exact-newline file IO, posix paths, shell detection (Git Bash / PowerShell), process trees, atomic writes
backend/bitbucket.py — git status, branch/commit/push, Bitbucket Server + Cloud pull requests, PR description draft
backend/tools.py — tool implementations + registry (read, list, glob, grep, repomap, write, edit, multiedit, apply_patch, bash (+ background), bash_output, bash_kill, show, open, webfetch, todo, question, notes, step, task); path sandbox (workspace, app data fence, .git read-only, symlinks)
backend/flow.py — savvy-flow engine: architect + per-module planning, DAG scheduler, auto-verify/fix, review, gates, integration, close; run_worker
backend/prompts.py — system prompts: base, build, plan, orchestrator phases, workers per tier
backend/session.py — Session, MessageSink (transcript writer + events), SessionStore (data/sessions/*.json)
backend/tests/ — pytest suite (uses scripts/fake_llm.py over real HTTP): test_tools, test_harness, test_app, test_long_run, test_platform, test_supercode, test_mcp (+ mcp_mini_server.py, mcp_sdk_server.py), test_extensions, test_workspace (modes, terminal, files, preview, local-only, HTTP guard), test_data (+ mcp_sqlite_server.py), test_security (sandbox, approvals, masking, privacy report, redirects)

frontend/ — React UI (Vite)
frontend/src/App.tsx — layout, session loading + SSE, commands, panels, permission mode, terminal state, file viewer / preview / data view wiring
frontend/src/api.ts — REST + EventSource client
frontend/src/types.ts — mirrors backend JSON shapes and events
frontend/src/sessionReducer.ts — applies SSE events to the open session
frontend/src/i18n.ts — UI strings (English only)
frontend/src/util.ts — formatting helpers, tier list, agent colors
frontend/src/index.css — theme variables, markdown, animations (tier crew, shimmer)
frontend/src/components/Sidebar.tsx — sessions list, LLM status, settings entry
frontend/src/components/Transcript.tsx — message/part rendering
frontend/src/components/ReasoningBlock.tsx — thinking card: live shimmer + preview, then "Thought for Ns" step timeline
frontend/src/components/ActivityView.tsx — activity view (overview, timeline, reasoning, plan, validation, actions, files, extensions) + PDF / Markdown export
frontend/src/components/McpSettings.tsx — Settings > MCP: servers, presets, JSON import, test, per-tool switches, project approval
frontend/src/components/McpChip.tsx — composer chip: MCP servers per session, project approvals
frontend/src/components/SkillsManager.tsx — Settings > Skills: list, enable, edit SKILL.md + files, AI draft, import
frontend/src/components/ModsManager.tsx — Settings > Mods: enable, options, source editor, logs, create (/mod or blank)
frontend/src/components/ReverseSettings.tsx — Settings > Reverse eng.: REA layer, disclaimer gate, readiness check
frontend/src/components/ModUi.tsx — mod status line, band above the prompt, side-panel sections, toasts
frontend/src/components/ToolPartView.tsx — one tool call (status, diff, output, data result table, open in editor / Data view)
frontend/src/components/TerminalPanel.tsx — integrated terminal: process tabs, live output, stdin, stop, history, detected URLs
frontend/src/components/ExplorerPanel.tsx — Files tab: project tree (new file / folder, dot files) + changes view
frontend/src/components/FileViewer.tsx — file editor / viewer: line numbers, edit + Ctrl+S, Markdown / HTML / image / PDF preview, open with the system, mention, data view
frontend/src/components/PreviewPanel.tsx — Preview tab: detected dev-server URLs, workspace HTML (sandboxed), device widths
frontend/src/components/DataView.tsx — data workspace: sources and tables, SQL editor, datasets, history, profile, chart, exports, chat strip
frontend/src/components/DataGrid.tsx — data grid: search, filters, sort, hidden columns, selection, copy, context menu, paging
frontend/src/components/PrivacySettings.tsx — Settings > Privacy & security: local-only toggles, security checks, destinations
frontend/src/components/PlanCard.tsx — savvy-flow plan + approve
frontend/src/components/ReviewCard.tsx — orchestrator review verdict
frontend/src/components/ProgressBar.tsx — savvy-progress bar
frontend/src/components/SidePanel.tsx — right panel tabs: Agents, Plan, Files, Preview, Git, Trace, Mods
frontend/src/components/AgentsPanel.tsx — savvy-progress agents panel (runs, planned tasks, lazy-loaded transcripts)
frontend/src/components/PlanBoard.tsx — modules, tasks, waves, critical path, checks, notes
frontend/src/components/FilesPanel.tsx — files changed since session start, diff, revert
frontend/src/components/TracePanel.tsx — live journal with filters, report export
frontend/src/components/CommandPalette.tsx — Ctrl+K palette
frontend/src/components/QuestionBar.tsx — answers to the question tool
frontend/src/components/ProjectDialog.tsx — new project / open folder with a folder browser (drives, recent)
frontend/src/components/GitPanel.tsx — git status and Bitbucket pull request form
frontend/src/components/TierIcon.tsx — tier crew icons + chips
frontend/src/components/Composer.tsx — prompt, agent switch, permission mode chip (Shift+Tab), ! shell mode, / commands, @ files, queue, history, drafts, status bar
frontend/src/notify.ts — desktop notifications + title badge
frontend/src/components/PermissionBar.tsx — pending permission prompt with the reason and a preview (command, diff, new file, SQL)
frontend/src/components/TodoList.tsx — session todos
frontend/src/components/SettingsModal.tsx — LLM, tiers, workspace (permission mode, permissions, data, terminal), privacy & security, Bitbucket, flow, context, agent, MCP, skills, mods, reverse engineering
frontend/src/components/Markdown.tsx, DiffView.tsx — renderers

scripts/fake_llm.py — scripted fake LLM (Ollama + OpenAI wire formats, <think> reasoning; demo prompts "run …", "create notes.md", "sales by region") for demos and tests
data/ — runtime config and sessions (git-ignored)
