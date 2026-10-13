# DOINg.AI SuperCode

**An [OpenCode](https://opencode.ai)-style coding agent for your local LLM (the same one as NEXUS.AI), built with React + Python, with the [savvy-flow](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-flow) orchestrator and the [savvy-progress](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-progress) agents panel, designed for long unattended sessions (10-15 h) that build whole applications.**

- **OpenCode agents**: `Build` (reads, edits, runs commands) and `Plan` (read-only); `Tab` switches.
- **savvy-flow** (`/flow <task>`): the orchestrator splits the work into homogeneous modules and tasks, runs tiered workers in parallel (`fable` / `heavy` / `careful` / `medium` / `light`), verifies and reviews every result adversarially, and closes with project-wide checks.
- **Permission modes like Claude Code**: **Ask** (edits, commands, app launches and data writes are confirmed, with a preview of the diff / command / SQL), **Accept edits** (file edits apply directly) and **Auto** (no human in the loop: plans approved, permissions granted except deny rules, the agents answer their own questions). `Shift+Tab` cycles.
- **Terminal, files and preview**: an integrated terminal (`` Ctrl+` ``) where you and the agents run commands and dev servers, `!command` shell mode, a file explorer and editor, open-with-the-system-app, and a **live preview** of the app being built (local dev servers or workspace HTML).
- **Data analysis add-in**: connect databases through the MCP servers you configured (PostgreSQL, SQLite, MySQL, DuckDB…) or open SQLite / CSV / XLSX / JSON files, browse tables, write SQL, filter / sort / profile / chart in a grid, export **CSV or Excel** - and ask the agent to do any of it from the chat.
- **Projects anywhere**: create a new project folder or open an existing one with a built-in folder browser (drives on Windows, recent projects).
- **Bitbucket pull requests** from the app: branch, commit, push and PR to **Bitbucket Server / Data Center** (enterprise, self-hosted) or **Bitbucket Cloud**, manually (`/pr`) or automatically at the end of a flow.
- **Full traceability**: a journal of every tool call and decision, a Markdown report, `docs/PLAN.md` and `docs/FLOW_REPORT.md`, git snapshots with undo.
- **Readable thinking + Activity view + PDF report**: the model's reasoning streams in a live "Thinking…" card and folds into a "Thought for 12s · 4 steps" timeline; the **Activity** view gathers every reasoning step, the savvy plan, every test / verification and every action, and exports a structured, professional **PDF** (or Markdown) report in English.
- **MCP servers**: connect any Model Context Protocol server (stdio, Streamable HTTP, SSE) and use its tools in every session, with tool search for large servers, per-server permissions and project `.mcp.json` approval.
- **Skills**: create, edit, generate with AI, import and run Agent Skills (`SKILL.md` folders, Claude Code compatible); the agents load them on demand.
- **Mods**: Claude Code-style hooks on the agent's events, created by asking (`/mod <description>`), with the **top 5** built in: status line, format-on-edit, safety guard, diagnostics, commit commands.
- **Reverse engineering layer** (optional): [REA](https://github.com/morluto/rea)'s MCP server and skill, gated behind a responsible-use acknowledgement.
- **Windows, macOS and Linux**: tested on all three in CI.
- **100% local, audited**: no telemetry, no cloud service; **local-only mode** (on by default) refuses any request the app would make to the internet, and Settings → **Privacy & security** lists every destination your data can reach. See [`SECURITY.md`](SECURITY.md). Providers: **Ollama**, **OpenAI-compatible** servers (LM Studio, vLLM, llama.cpp, LocalAI…) and **n8n** webhooks.

---

## Quick start

Prerequisites: **Python 3.10+**, **Node.js 20+**, and **Git** (Git for Windows on Windows: it also provides Git Bash, which the agents use for shell commands).

**Windows**

```cmd
install.bat
start.bat
```

`install.bat` finds Python (the `py` launcher first), creates `.venv`, installs the backend, builds the UI and can add a desktop shortcut. `start.bat` serves everything on <http://localhost:4001> and opens the browser.

**macOS / Linux**

```bash
./install.sh
./start.sh            # http://localhost:4001
```

Development with hot reload: `start-dev.bat`, or `python server.py` plus `cd frontend && npm run dev` (<http://localhost:4000>). Ports 4000/4001 do not collide with NEXUS.AI (3000/3001).

## Connect your local LLM (NEXUS.AI)

1. **Automatic**: on first start, if a `NEXUS.AI` (or `nexus.ai`) folder sits next to this one, its `llmConfig` (provider, URL, key, model) is imported from its `db.json` (following `server-config.json` when the database was moved).
2. **Settings → Local LLM → Import from NEXUS.AI**: give the NEXUS.AI folder or its `db.json`.
3. **By hand** in Settings → Local LLM, then **Test connection** (lists the models; click one to use it).

| Provider | Example URL | Notes |
| --- | --- | --- |
| Ollama | `http://localhost:11434` | `ollama pull qwen2.5-coder:14b` |
| OpenAI-compatible | `http://localhost:1234` (LM Studio), `http://localhost:8000` (vLLM) | optional Bearer key |
| n8n | `https://your-n8n/webhook/...` | receives `{prompt, messages, model}`, returns text |

**Tool calling.** In `auto` mode, native tool calling is used, with an automatic fallback to a text protocol (`<tool_call>{json}</tool_call>`, Hermes/Qwen style) when the server or model refuses tools. `<think>` blocks of reasoning models are shown separately, folded.

**Models.** Use a coder model that handles tools (Qwen2.5-Coder, Qwen3, Devstral, Llama 3.1+…). Give the strongest one to the **orchestrator** (Settings → Savvy tiers) and lighter ones to `light` / `medium`. Raise the **context window** if your server allows it (sent to Ollama as `num_ctx`). Answers are written in English (Settings → Local LLM → language of the agents' answers).

## Projects

**New session** (or `Alt+N`, `/new`) opens the project dialog:

- **New project**: pick the parent folder in the browser (any drive), type a name; the folder is created, optionally with `git init`. The default parent is Settings → Workspace → *Default location for new projects* (`~/SuperCodeProjects`).
- **Open folder**: browse to an existing project (folders that look like projects are highlighted) or pick a recent one.
- **Start in auto mode**: the session runs without any approval.

Agents can never read or write outside the session folder.

## Permission modes

The chip under the prompt shows the session's mode; click it or press `Shift+Tab` to cycle. The default for new sessions is in Settings → Workspace; a project can start in Auto from the project dialog.

| Action of an agent | **Ask** (default) | **Accept edits** | **Auto** |
| --- | --- | --- | --- |
| Read, search, list files | runs | runs | runs |
| Create / edit / delete files | asks, with the diff or the new content | runs | runs |
| Shell command (`bash`) | asks (allow rules such as `git status*` run directly) | asks | runs |
| Command that reaches the network (`curl`, `git push`, `ssh`…) with local-only on | asks, with the reason | asks | **refused** (never unattended) |
| Open a file / app with the system (`open`) | asks | asks | runs |
| SQL that changes data (`INSERT`, `UPDATE`, `DELETE`, DDL…) | asks, with the SQL | asks | runs |
| Approve the savvy-flow plan, answer questions | you | you | automatic, assumptions stated |
| Deny rules (`git push*`, `sudo *`, `rm -rf /*`…), plan agent read-only, sandbox | apply | apply | apply |

Every prompt offers *Allow once*, *Always (this session)* - remembered per command pattern (`npm test*`), per file glob or per category - and *Reject*. Switching to a more permissive mode while a prompt is open approves it if the new mode would not ask. Settings → Workspace sets each category (*allow* / *ask* / *deny*) and the shell rules. In Auto, the agents' system prompts say no human is watching, so they never stop to wait; combine it with a token budget (Settings → Flow) for unattended nights.

## Terminal, files and preview

- **Terminal** (`` Ctrl+` ``, header button, `/terminal`): run any command in the project folder (`cd` is remembered), output streams live, send input to a running program, `Ctrl+C` / stop, several processes in tabs. Background processes the agents start (`bash` with `run_in_background`, e.g. a dev server) appear there too, with a robot icon; the agents read their output (`bash_output`) and stop them (`bash_kill`).
- **Shell mode**: start a prompt with `!` (`!npm test`) - the command runs right away (not through the agent) and its output is shared with the agent on your next message, like Claude Code's `!`.
- **Files** tab: the project tree (new file / folder, dot files), click to open the **editor** (line numbers, edit and `Ctrl+S` with conflict detection, Markdown / HTML / image / PDF preview, *open with the system app*, *mention in the chat*, open data files in the Data view). The *Changes* view keeps the per-file diffs and one-click restore. File cards in the chat open the file at the right line.
- **Preview** tab: the app you are building, live. URLs printed by dev servers (`http://localhost:5173`) are detected and listed; workspace HTML files are served sandboxed; desktop / tablet / mobile widths, reload, wide panel. The agents can show you something with the `show` tool (a file at a line, or a local URL in the preview) and open files or apps with the system (`open`, which asks in Ask mode).

## Data analysis

Header **Data** button or `/data` opens the data workspace:

- **Sources**: every MCP server you configured that has a SQL query tool (PostgreSQL, SQLite, MySQL, DuckDB, ClickHouse… - detected from the tool names and schemas), plus the SQLite databases and CSV / TSV / JSON / JSONL / XLSX files of the project (loaded into an in-memory SQLite table so you can query them in SQL). Click a source to list its tables, a table to open it.
- **SQL editor** (`Ctrl+Enter`), query history; statements that change data ask for confirmation unless the session is in Auto mode.
- **Grid**: search, filters (= ≠ contains, > <, empty, in, regex…; right-click a cell to filter on its value or exclude it), sort (Shift+click for several keys), hide columns, select rows, copy as TSV, pagination; **Profile** (types, empties, distinct, min / max / mean / median, top values, duplicates) and **Chart** (count / sum / avg / min / max by a column, bars or line).
- **Export** the current view - or every row with Shift - to **CSV** (UTF-8 BOM, Excel-safe), **Excel (.xlsx)** or JSON.
- **The agent sees the same tables**: the chat strip at the bottom sends your request with the dataset and its view as context ("only the rows of 2024, sorted by revenue", "check for duplicates", "write the SQL for the monthly totals"). The agent uses `data_sources`, `data_query`, `data_view` (changes what you see in the grid), `data_profile` and `data_export`; its query results also show as small tables in the chat with *Open in the Data view*. Only the first rows of a result go to the LLM (Settings → Workspace → *Rows the agent sees*); everything else stays in the app.

## Privacy & security

The app has **no telemetry and no cloud service**: your code, prompts and data only go to the endpoints you configure. Settings → **Privacy & security** shows them all with their status, and the security checks in force:

- **Local-only mode** (default): every HTTP request the app makes - LLM, MCP servers over HTTP, `webfetch`, Bitbucket - is checked (redirects included) and refused unless it goes to this machine or a private network. Shell commands that reach the network always ask first and never run unattended in Auto mode; package installs can be allowed or not.
- **The server** listens on `127.0.0.1` only; it rejects requests whose `Host` is not this machine (DNS rebinding) and unsafe requests from other origins (CSRF), so a web page open in your browser cannot drive the agents. To use it from another machine, `python server.py --host 0.0.0.0`: an access token is then required (generated and printed, or `--token <secret>`).
- **The agents' sandbox**: no file access outside the project, never the app's own data folder (settings with secrets, sessions), `.git` read-only, symlinks pointing outside skipped, executables never opened with the system.
- **Secrets** (LLM key, Bitbucket token, MCP env / headers / URL credentials / command-line keys) are masked in the UI and never sent back to the browser.
- **MCP servers** declared by a project stay inert until approved, and any change to their definition needs a new approval; user mods only load once installed.

The audit, the data flows and the residual risks are documented in [`SECURITY.md`](SECURITY.md).

## Bitbucket pull requests

Settings → **Bitbucket**:

| Field | Server / Data Center (enterprise) | Cloud |
| --- | --- | --- |
| Server URL | `https://bitbucket.company.com` (with context path if any) | empty |
| Project / workspace, repository | empty = read from the git remote (`https://host/scm/PROJ/repo.git`, `ssh://git@host:7999/proj/repo.git`) | empty = from `bitbucket.org/workspace/repo` |
| Token | an **HTTP access token** (project or repository, *write*) or a personal access token | an **app password / API token** with *Repositories: write* and *Pull requests: write* |
| Username | optional (empty = Bearer token) | your Bitbucket username / email |

Then **Test Bitbucket**. From a session, open the **Git** tab (or `/pr`):

1. the title and the description are drafted from the session (plan, tasks with their checks, integration results, changed files), at no token cost;
2. **Create pull request**: if you are on a shared branch (`main`, `master`, `develop`, `release/*`…) a branch `supercode/<title>-<date>` is created, changes are committed, the branch is pushed (your git credentials; for HTTPS remotes the token is also passed for this push only, never stored in git), and the PR is opened against the target branch (default: the repository's default branch) with the configured reviewers;
3. if a PR already exists for the branch, its link is returned.

*Open a pull request automatically when a savvy-flow finishes* does all of this at the end of each flow. Every PR is recorded in the journal and listed in the Git tab. Tokens are masked in the UI and never sent back to the browser.

## Using it

| Key / command | Effect |
| --- | --- |
| `Enter` / `Shift+Enter` | send / new line (while the agent works, the message is **queued**) |
| `Tab` | Build → Plan → Savvy Flow |
| `Shift+Tab` | permission mode: Ask → Accept edits → Auto |
| `!command` | shell mode: run a command now, share its output with the agent |
| `` Ctrl+` `` | terminal |
| `↑` / `↓` | prompt history (drafts kept per session) |
| `Esc` | interrupt |
| `@path` | attach a file (autocomplete) |
| `Ctrl+K` · `Alt+N` · `Ctrl+B` · `Ctrl+.` | palette · new session · sidebar · side panel |
| `/flow <task>` | run savvy-flow |
| `/goal <condition>` | goal mode: keep working until an evaluator confirms the condition |
| `/pr` | Bitbucket pull request |
| `/undo` | undo the last turn (files **and** conversation, via git snapshot) |
| `/auto` · `/compact` · `/fork` · `/report` | auto mode on / off · summarize the context · branch the session · download the report |
| `/terminal` · `/files` · `/preview` · `/data` | terminal · file explorer · live preview · data analysis |
| `/activity` | every reasoning step, the plan, tests and actions; **Export PDF** |
| `/<skill-name> <args>` | run a skill (e.g. `/code-review src/`, `/systematic-debugging <error>`) |
| `/mod <description>` · `/skill-new <need>` | the agent writes and installs a mod · writes a skill |
| `/commit` · `/clean-gone` | commit everything with an AI-written message · delete branches whose remote is gone (commit-commands mod) |
| `/reverse <target> <question>` | reverse engineering with REA (optional layer) |
| `/mcp` · `/skills` · `/mods` | open their settings |
| `/build`, `/plan`, `/agents`, `/settings`, `/init`, `/help` | — |

`/init` creates or updates `AGENTS.md`; that file (or `CLAUDE.md`) is injected into every agent's prompt.

**Permissions**: see [Permission modes](#permission-modes). **Shell rules** (`git status* = allow`, `git push* = deny`…) apply per sub-command (`&&`, `;`, `|`); an *allow* rule never covers a command with substitutions, redirections or exec-style options (`$(…)`, `> file`, `find -exec`, `git -c`…). Approving a plan pre-approves its verification commands and the edits of its task files.

## Thinking, activity and the PDF report

- **Thinking**: while a model reasons (Ollama `thinking`, OpenAI-compatible `reasoning_content`, or `<think>` tags), a card shows a shimmering *Thinking…* header, a timer and a rolling preview of the thought. When the answer starts it folds into a pill - *Thought for 12s · 4 steps* - that opens a step-by-step timeline (paragraphs, numbered lines or sentence groups).
- **Activity view** (header button or `/activity`): overview with key figures (span, requests, reasoning steps, actions, files, checks passed, tokens) and the outcome; a filterable **timeline** of everything; every **reasoning** step of the main agent and of each savvy worker; the **savvy plan** (modules, tasks with tier, size, status, rounds, duration, defects, worker reports, spec, design); **validation** (verify commands, test / build / lint runs with PASS/FAIL, orchestrator reviews); **actions** (stats per tool + full log); **files** changed with +/-; **extensions** (MCP calls, skills used, mod events, blocked actions, permission decisions, questions, pull requests, issues). It refreshes live while the agents work.
- **Export PDF**: an A4 report in English - cover page with key figures, table of contents and PDF bookmarks, executive summary, requests, plan, execution, validation, reasoning (summary, full or none), action log, files, extensions and decisions, issues - with running header and footer. Markdown export too. Generated locally with reportlab (system fonts, bundled fallback).

## MCP servers

Settings → **MCP** (or `/mcp`, or the **MCP** chip above the prompt):

- **Add** a server (stdio command, Streamable HTTP or legacy SSE; env, headers, timeout), **import** a Claude Desktop / Claude Code (`{"mcpServers": …}`) or VS Code (`{"servers": …}`) JSON, or start from a **preset** (Context7 docs, filesystem, git, fetch, memory, sequential thinking, Playwright, GitHub, Atlassian, PostgreSQL).
- **Test** shows the server, its tools (read-only ones marked) and lets you switch tools off one by one.
- **Project servers** declared in a repository's `.mcp.json` never start until you approve them.
- In sessions, tools appear as `mcp__<server>__<tool>`. Beyond **16** tools (configurable) the agents get `mcp_search` + `mcp_call` instead of every schema - tool search keeps a local model's prompt small and stable. Server instructions are summarized in the system prompt; `mcp_resources` lists / reads resources.
- **Best practices built in**: servers start lazily and restart after a crash; per-server permission (*ask* / *allow* / *deny*), read-only tools trusted, "always" grants per server, auto mode honoured, deny always wins; the plan agent and the orchestrator only see read-only tools; results are normalized to text (images summarized) and long outputs spill to a file; secrets in env / headers are masked in the UI; switch servers per session from the chip.
- The client speaks the protocol directly (JSON-RPC 2.0, protocol 2025-06-18): it is tested against the official Python SDK over stdio and Streamable HTTP, and works on Windows (`npx.cmd`, process trees).

## Skills

Settings → **Skills** (or `/skills`). A skill is a folder with a `SKILL.md` - YAML frontmatter (`name`, `description`, optional `argument-hint`, `allowed-tools`, `disable-model-invocation`, `user-invocable`) and Markdown instructions - plus optional reference files and scripts: the [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) format used by Claude Code.

- **Progressive disclosure**: the agents only see each skill's name and description; they load the body with the `skill` tool when a task matches, then read reference files as needed. Run one yourself with `/<skill-name> <arguments>` (`$ARGUMENTS` is substituted).
- **Where**: personal skills in `data/skills`, project skills in `<project>/.supercode/skills` (also read: `.claude/skills`, `.agents/skills`), built-in skills shipped with the app. A project skill overrides a personal one with the same name.
- **Create**: blank template, **Draft with AI** from a description (or improve the open skill), `/skill-new <need>` to let the agent research and write it, or **import** a folder. Edit `SKILL.md` and reference files in the app; duplicate a built-in skill to customize it; switch any skill off.
- **Built in**: `skill-creator`, `mod-creator`, `code-review`, `test-driven-development`, `systematic-debugging` (and `reverse-engineer-anything` with the reverse layer).

## Mods

Mods extend the agent like Claude Code mods. A mod is a folder with `mod.json` and a `mod.py` exposing `register(on, mod)`; every hook is `(ctx, e, next)`: call `next(e)` to continue (or rewrite the event), return `{"deny": reason}` to block.

| Event | Use it to |
| --- | --- |
| `tool.call` (matcher on the tool) | block or rewrite a call before it runs, post-process the result the model sees |
| `prompt.submit` · `prompt.compose` | rewrite the user's prompt · add system-prompt sections |
| `turn.complete` · `flow.phase` · `session.start` | react to turns, savvy-flow phases, sessions |
| `command.run` | answer a slash command declared with `mod.command()` |

`ctx` gives the status line (`ctx.status`), a band above the prompt with buttons (`ctx.band`), a section in the side panel's **Mods** tab (`ctx.panel`), toasts, session `state` and global `store`, `await ctx.run(cmd)`, `ctx.read`, `await ctx.llm(prompt)`, options and the journal. Mods can also give the agent tools (`mod.tool`). A failing mod is isolated, logged and reported, never fatal.

- **Create by asking**: `/mod block edits under migrations/ and count them in the status line` - the mod builder writes the mod in `data/mods`, validates it, and installs it after your approval (permission *mods*). Or edit / create mods in Settings → **Mods** (source editor, options, logs, reload).
- **Top 5 built in** (the most used Claude Code plugins / hooks that make sense here, all on by default):
  1. **statusline** - branch and uncommitted files, model, context %, tokens, last turn time (ccstatusline-style);
  2. **format-on-edit** - Prettier / Ruff / Black / gofmt / rustfmt on every file the agent writes (only formatters the project configures);
  3. **safety-guard** - blocks `rm -rf /`, `curl | sh`, force push, `DROP DATABASE`, disk writes, publishing… and edits to `.env`, keys and `.git` - even in auto mode;
  4. **diagnostics** - Python syntax (+ Ruff errors), JSON, `node --check`, `tsc` after every edit, returned to the agent so it fixes them at once (OpenCode's LSP diagnostics);
  5. **commit-commands** - `/commit` with a Conventional Commits message written by your model, `/clean-gone`, and a "commit" band after turns that changed files (Anthropic's commit-commands plugin).

## Reverse engineering layer (optional)

Settings → **Reverse eng.**: an opt-in integration of [REA - Reverse Engineer Anything](https://github.com/morluto/rea) (MIT) with its exact feature set:

- REA's **MCP server**, pinned (`npx -y rea-agents@6.4.0 mcp`, named `rea`): native binaries through Hopper / Ghidra / IDA, Go / Rust / .NET / WASM / EVM artifacts, JavaScript and Electron apps, websites and network captures, Android, firmware, SQLite, recorded crashes and process behavior - with evidence for every conclusion;
- REA's **`reverse-engineer-anything` skill**, vendored with its references, license and disclaimer, so the agent follows REA's evidence-first workflows;
- `/reverse <target> <question>` in any session; provider choice (`REA_ANALYSIS_PROVIDER`), compact tool schemas for local models (`REA_MCP_INPUT_SCHEMA_PROFILE=compact`), extra REA environment, long timeouts, and a **readiness check** (`rea doctor`).

It is off by default and can only be enabled after acknowledging REA's responsible-use terms (lawful analysis of software you own or are authorized to examine). Requires Node.js 22.19+, 24.11+ or 26+; REA tools ask for permission like any MCP tool.

## savvy-flow

```
 /flow <task>
     │
     ▼
 PLAN ── architect pass: read the project (repo map), split into homogeneous modules with contracts
     │   (small request: tasks directly); then one short JSON call per module -> tasks with size,
     │   complexity, owned files, dependencies, self-contained brief and verify command
     ▼
 [your approval]  ← skipped in auto mode; or reply in the prompt to revise the plan
     ▼
 DESIGN (UI work: design specs injected into each brief, workers never invent UI)
     ▼
 DELEGATE ── tiered workers along the DAG, in parallel, longest chains first
     │          each worker reports its steps (savvy-progress panel)
     ▼
 VERIFY TASK ── the task's check runs; on failure the worker fixes it itself (auto-fix)
     ▼
 REVIEW ── the orchestrator reviews the real diff and the check results
     │       accept → accepted (+ optional git commit, module gate)
     │       return → back with the defects, same tier or higher (2 rounds + 1 escalation)
     │       a light task whose check passes is accepted without an LLM review
     ▼
 VERIFY ── project-wide checks; failures become fix tasks
     ▼
 CLOSE ── summary + docs/FLOW_REPORT.md (+ optional Bitbucket PR)
```

### Splitting the initial plan

`backend/planner.py` turns the orchestrator's proposal into a schedulable graph:

- **splits** oversized tasks (size L with complexity ≥ 4, huge briefs, too many files) into 2-4 subtasks;
- **merges** chains of tiny tasks (fewer worker start-ups, fewer tokens);
- checks each **tier** against the complexity (a complexity-5 task never goes to a light worker);
- allows dependencies **backwards only** (no cycle possible) and links modules through their exit tasks;
- **serializes file conflicts**: two tasks touching the same files never run at the same time;
- computes **waves** and the **critical path**: the scheduler starts the longest chains first, up to `maxParallel` workers.

Then come module gates (each module's check once all its tasks are accepted) and the integration phase (project checks, automatic fix tasks).

| Tier | Default effort | Tool budget | For |
| --- | --- | --- | --- |
| 🧑‍🚀 fable | high | 30 | hardest logic and bugs (reserve) |
| 🕵️ heavy | xhigh | 40 | unfamiliar code, unclear root cause |
| 🧑‍🔧 careful | high | 30 | delicate changes, invariants |
| 🧑‍🍳 medium | medium | 20 | standard feature work |
| 🏎️ light | low | 12 | mechanical edits, builds, tests |

## Harness: what makes 10-15 hour runs hold

Built from the code of [OpenCode](https://github.com/sst/opencode) and [OpenClaude](https://github.com/Twigpine/openclaude):

| # | Improvement | Effect |
| --- | --- | --- |
| 1 | tolerant `edit` (exact → normalized quotes/dashes → trailing spaces → indentation; line-number prefixes stripped; CRLF kept) | far fewer failed edits with local models |
| 2 | atomic `multiedit` | several changes to one file in one call, all or nothing |
| 3 | `apply_patch` (OpenCode format) | create / update / move / delete several files in one call |
| 4 | read-before-edit + "changed since you read it" detection | parallel workers never overwrite each other |
| 5 | per-file locks | concurrent writes serialized |
| 6 | long outputs cut, full text saved to a readable file | fewer tokens, nothing lost |
| 7 | pruning of old tool outputs | stable context over hundreds of calls |
| 8 | structured checkpoint compaction (objective, decisions, state, next move, files), brief pinned | long tasks never overflow the context |
| 9 | recovery from context-overflow errors | forced compaction, then retry |
| 10 | retries with exponential backoff + jitter (connection, 5xx, 429, timeouts) | a hiccuping LLM server does not kill the night |
| 11 | doom-loop guard + nudge after repeated failures | no tokens burnt in loops |
| 12 | step budget with a forced final report | every worker ends cleanly |
| 13 | shadow git snapshots (`/undo`, changed files, diff, per-file revert) | everything is reversible, your `.git` untouched |
| 14 | automatic resume of flows after a crash or restart | a 3 a.m. power cut does not lose the night |
| 15 | pattern permission rules + "always" per command + auto mode | no session stuck on a prompt |
| 16 | `repomap` tool (most referenced files + signatures), injected into planning | less exploration, better plans |
| 17 | `question`, `webfetch`, `notes` (contracts shared between workers) | explicit decisions, docs at hand, consistency in parallel |
| 18 | goal mode (`/goal`) with an evaluator | continuous work until a checkable criterion holds |
| 19 | auto-verify + auto-fix before review, tier escalation, per-task timeout | fewer review rounds, no stuck task |
| 20 | JSONL journal + Markdown report + `docs/PLAN.md` / `docs/FLOW_REPORT.md` + traced git commits | full traceability of what was done, by whom, with what result |

Plus: session fork, manual compaction, system prompts with a stable prefix (KV-cache reuse on local servers), per-run transcript files, event sequence numbers (no duplicated stream after a reconnect).

## Interface

1. **Command palette** (`Ctrl+K`): commands, panels, sessions.
2. **Message queue**: type while the agent works; sent as soon as it is free.
3. **Prompt history** (`↑/↓`) and per-session drafts.
4. **Side panel tabs**: *Agents* (savvy-progress), *Plan* (modules, waves, critical path, checks, tokens and time per task), *Files* (explorer + changes), *Preview*, *Git*, *Trace*, *Mods*.
5. **Changed files**: per-file diff and one-click restore.
6. **Trace**: live, filterable journal (decisions, tools, errors) and report export.
7. **Desktop notifications** and tab badge (permission, question, flow done).
8. **Grouped tool calls**, "jump to latest", scrolling that stays pinned.
9. **Status bar**: context gauge, tokens, compactions, goal, **permission mode** chip.
10. **Message actions**: copy, retry, edit and resend, fork from here; clickable answers to agent questions.
11. **Thinking cards**: live shimmer and preview, then a "Thought for Ns · steps" timeline.
12. **Activity view** with filters and **Export PDF / Markdown**.
13. **Slash menu** with the app's commands, mod commands and skills; **MCP chip** to switch servers per session.
14. **Mod status line** under the prompt, mod **bands** with buttons above it, **toasts**, and a **Mods** side-panel tab.
15. **Terminal** panel (resizable), **file editor**, **live preview**, **Data view**; permission prompts with the diff, the new file, the command or the SQL, and why it asks.

## Windows notes

- Shell commands run in **Git Bash** when Git for Windows is installed (plans and agents speak bash), else in **PowerShell** (the agents are told which shell they have). Force one in Settings → Workspace → *Shell for commands*.
- Files keep their line endings: CRLF files stay CRLF, new files are written as given (no silent LF→CRLF conversion).
- Commands run without a console window, and timeouts / `Esc` kill the whole process tree (`taskkill /T`).
- Paths shown to the agents and in the UI use `/`; any drive can be browsed in the project dialog.
- Saves retry when Windows briefly locks a file (antivirus, indexer).

## Try it without a GPU

A scripted fake LLM plays every role (Ollama + OpenAI-compatible, with streaming):

```bash
python scripts/fake_llm.py --port 11500                     # native tools
python scripts/fake_llm.py --port 11500 --no-tools          # forces the text protocol
python scripts/fake_llm.py --port 11500 --plan-mode modular # modular plans
```

Then Settings → Local LLM → Ollama, `http://localhost:11500`, model `fake-coder:7b`. Try "run the tests", "create notes.md" (a permission prompt in Ask mode) or, with a `sales.csv` in the project, "show me sales by region" (a query result in the chat and the Data view).

## Architecture

```
 Browser ── React 19 · Vite · Tailwind ────────────────────────────────────────┐
 │ Sessions │ Transcript (thinking, text, tools, diffs, plan, │ Agents · Plan ·   │
 │          │ reviews, data tables) · progress · questions ·  │ Files · Preview · │
 │          │ permissions · mod band · prompt · terminal      │ Git · Trace · Mods│
 │ Activity (PDF) · Data view · file editor · Settings: … Privacy · MCP · Mods  │
 └──────────────── REST /api/*  +  SSE /api/sessions/{id}/events ───────────────┘
                                   │
 FastAPI (server.py → backend/) ───┴──────────────────────────────────────────────┐
 │ agent.py      agent loop: LLM ⇄ tools, retries, compaction, loop guard        │
 │ flow.py       savvy-flow: plan → design → delegate → review → verify → close  │
 │ planner.py    decomposition: modules, DAG, conflicts, waves, critical path    │
 │ tools.py      read/edit/multiedit/apply_patch/bash/repomap/... (sandboxed)     │
 │ processes.py  terminal + background processes (live output, URLs, stdin)      │
 │ workspace_api terminal, ! shell, file tree / editor, raw files, preview       │
 │ data.py       data add-in: sources, SQL, datasets, views, exports, data tools │
 │ netguard.py   local-only mode for every outbound request · security.py guard │
 │ bitbucket.py  branch, commit, push, pull request (Server / Cloud)            │
 │ extensions.py what MCP, skills and mods add to each agent run                 │
 │ mcp_client.py MCP protocol client (stdio, Streamable HTTP, SSE) + manager     │
 │ mcp_tools.py  MCP tools for agents: tool search, permissions, presets, REA    │
 │ skills.py     Agent Skills: discovery, skill tool, /commands, editing, AI     │
 │ mods.py       mod loader, hook chains, ctx API (+ builtin/mods, builtin/skills)│
 │ report.py     activity collection, Markdown and PDF report                    │
 │ osutil.py     Windows/macOS/Linux: shells, processes, line endings, paths     │
 │ snapshot · journal · compaction · permissions · repomap · editing · llm       │
 └──────────────────────────────────┬─────────────────────────────────────────────┘
                                    ▼
                     your local LLM (as in NEXUS.AI)
```

Settings and sessions live in `data/` (`ORCH_DATA_DIR` to move it). No cross-origin request is accepted (the dev UI goes through Vite's proxy; add origins with `ORCH_CORS_ORIGINS` if you really need to): another web page open in your browser cannot drive the agents or install a mod. See [`filemap.md`](filemap.md) for every file and [`SECURITY.md`](SECURITY.md) for the security model.

## Tests and CI

```bash
pip install -r requirements.txt
python -m pytest            # 130 tests: editing, patches, permissions, planner, snapshots, compaction,
                            # retries, overflow, auto-fix, budget, crash resume, undo/fork, goal,
                            # modular flow, parallel waves, auto mode, projects, Bitbucket Server + Cloud,
                            # CRLF, process trees, shells, MCP (stdio, HTTP, tool search, permissions,
                            # project approval; official SDK interop when `pip install mcp`), skills,
                            # mods (top 5, user mods, /mod builder, /commit), reasoning, activity + PDF,
                            # permission modes, local-only, HTTP guard, terminal / ! shell / background
                            # processes, files and preview API, data add-in (files, SQLite, MCP database
                            # server, views, exports, agent tools), sandbox and secret masking
cd frontend && npm run lint # TypeScript
```

GitHub Actions runs the backend tests, the frontend build and a server smoke test on **Windows, Ubuntu and macOS** for every pull request (`.github/workflows/ci.yml`).

## Credits

- Interface and agents inspired by [OpenCode](https://github.com/sst/opencode); harness ideas from [OpenClaude](https://github.com/Twigpine/openclaude).
- savvy-flow and savvy-progress: [JohnnyVizz/claude-kit](https://github.com/JohnnyVizz/claude-kit) (MIT), adapted to local LLMs.
- LLM configuration shared with NEXUS.AI / DOINg.AI.
- [Model Context Protocol](https://modelcontextprotocol.io); skills follow Anthropic's [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) format; mods follow Claude Code's mod / hook model; the built-in mods are inspired by ccstatusline, the Prettier / Black PostToolUse hooks, the dangerous-command PreToolUse guards, OpenCode's LSP diagnostics and Anthropic's commit-commands plugin.
- Reverse engineering layer: [REA](https://github.com/morluto/rea) by morluto (MIT); its skill is vendored in `backend/builtin/skills/reverse-engineer-anything` with its license and disclaimer.
