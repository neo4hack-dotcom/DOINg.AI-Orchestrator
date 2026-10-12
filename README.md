# DOINg.AI SuperCode

**An [OpenCode](https://opencode.ai)-style coding agent for your local LLM (the same one as NEXUS.AI), built with React + Python, with the [savvy-flow](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-flow) orchestrator and the [savvy-progress](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-progress) agents panel, designed for long unattended sessions (10-15 h) that build whole applications.**

- **OpenCode agents**: `Build` (reads, edits, runs commands) and `Plan` (read-only); `Tab` switches.
- **savvy-flow** (`/flow <task>`): the orchestrator splits the work into homogeneous modules and tasks, runs tiered workers in parallel (`fable` / `heavy` / `careful` / `medium` / `light`), verifies and reviews every result adversarially, and closes with project-wide checks.
- **Auto mode**: no human in the loop. Plans are approved automatically, permissions are granted (deny rules still apply), and the agents answer their own questions with stated assumptions.
- **Projects anywhere**: create a new project folder or open an existing one with a built-in folder browser (drives on Windows, recent projects).
- **Bitbucket pull requests** from the app: branch, commit, push and PR to **Bitbucket Server / Data Center** (enterprise, self-hosted) or **Bitbucket Cloud**, manually (`/pr`) or automatically at the end of a flow.
- **Full traceability**: a journal of every tool call and decision, a Markdown report, `docs/PLAN.md` and `docs/FLOW_REPORT.md`, git snapshots with undo.
- **Windows, macOS and Linux**: tested on all three in CI.
- **100% local**: no telemetry. Providers: **Ollama**, **OpenAI-compatible** servers (LM Studio, vLLM, llama.cpp, LocalAI…) and **n8n** webhooks.

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

## Auto mode

Turn it on for a whole installation (Settings → Workspace → *Auto mode by default*), per project (checkbox in the project dialog), or per session (the **Auto** chip under the prompt, `/auto`). In auto mode:

| Usually asks you | In auto mode |
| --- | --- |
| Approve the savvy-flow plan | approved automatically |
| Orchestrator questions about the request | the orchestrator answers them itself, listing its assumptions in the plan |
| `question` tool | answered with "choose the most reasonable option and state your assumption" |
| Permission prompts (shell, edits, web) | granted; **deny rules still apply** (`git push*`, `sudo *`, `rm -rf /*`, `rd /s*`, `format *`…) |

The agents' system prompts also say no human is watching, so they never stop to wait. Combine with a token budget (Settings → Flow) for unattended nights.

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
| `↑` / `↓` | prompt history (drafts kept per session) |
| `Esc` | interrupt |
| `@path` | attach a file (autocomplete) |
| `Ctrl+K` · `Alt+N` · `Ctrl+B` · `Ctrl+.` | palette · new session · sidebar · side panel |
| `/flow <task>` | run savvy-flow |
| `/goal <condition>` | goal mode: keep working until an evaluator confirms the condition |
| `/pr` | Bitbucket pull request |
| `/undo` | undo the last turn (files **and** conversation, via git snapshot) |
| `/auto` · `/compact` · `/fork` · `/report` | auto mode · summarize the context · branch the session · download the report |
| `/build`, `/plan`, `/agents`, `/settings`, `/init`, `/help` | — |

`/init` creates or updates `AGENTS.md`; that file (or `CLAUDE.md`) is injected into every agent's prompt.

**Permissions** (when auto mode is off): file edits allowed, shell on request (*once* / *always*, remembered per command pattern like `npm test*` / *reject*). **Shell rules** (`git status* = allow`, `git push* = deny`…) apply per sub-command (`&&`, `;`, `|`). Approving a plan pre-approves its verification commands.

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
4. **Side panel tabs**: *Agents* (savvy-progress), *Plan* (modules, waves, critical path, checks, tokens and time per task), *Files*, *Git*, *Trace*.
5. **Changed files**: per-file diff and one-click restore.
6. **Trace**: live, filterable journal (decisions, tools, errors) and report export.
7. **Desktop notifications** and tab badge (permission, question, flow done).
8. **Grouped tool calls**, "jump to latest", scrolling that stays pinned.
9. **Status bar**: context gauge, tokens, compactions, goal, **Auto** switch.
10. **Message actions**: copy, retry, edit and resend, fork from here; clickable answers to agent questions.

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

Then Settings → Local LLM → Ollama, `http://localhost:11500`, model `fake-coder:7b`.

## Architecture

```
 Browser ── React 19 · Vite · Tailwind ────────────────────────────────────────┐
 │ Sessions │ Transcript (text, tools, diffs, plan, reviews) │ Agents · Plan ·   │
 │          │ savvy-progress bar · questions · permissions    │ Files · Git ·     │
 │          │ prompt (queue, history, auto)                   │ Trace             │
 └──────────────── REST /api/*  +  SSE /api/sessions/{id}/events ───────────────┘
                                   │
 FastAPI (server.py → backend/) ───┴──────────────────────────────────────────────┐
 │ agent.py      agent loop: LLM ⇄ tools, retries, compaction, loop guard        │
 │ flow.py       savvy-flow: plan → design → delegate → review → verify → close  │
 │ planner.py    decomposition: modules, DAG, conflicts, waves, critical path    │
 │ tools.py      read/edit/multiedit/apply_patch/bash/repomap/... (sandboxed)     │
 │ bitbucket.py  branch, commit, push, pull request (Server / Cloud)            │
 │ osutil.py     Windows/macOS/Linux: shells, processes, line endings, paths     │
 │ snapshot · journal · compaction · permissions · repomap · editing · llm       │
 └──────────────────────────────────┬─────────────────────────────────────────────┘
                                    ▼
                     your local LLM (as in NEXUS.AI)
```

Settings and sessions live in `data/` (`ORCH_DATA_DIR` to move it). See [`filemap.md`](filemap.md) for every file.

## Tests and CI

```bash
pip install -r requirements.txt
python -m pytest            # 67 tests: editing, patches, permissions, planner, snapshots, compaction,
                            # retries, overflow, auto-fix, budget, crash resume, undo/fork, goal,
                            # modular flow, parallel waves, auto mode, projects, Bitbucket Server + Cloud,
                            # CRLF, process trees, shells
cd frontend && npm run lint # TypeScript
```

GitHub Actions runs the backend tests, the frontend build and a server smoke test on **Windows, Ubuntu and macOS** for every pull request (`.github/workflows/ci.yml`).

## Credits

- Interface and agents inspired by [OpenCode](https://github.com/sst/opencode); harness ideas from [OpenClaude](https://github.com/Twigpine/openclaude).
- savvy-flow and savvy-progress: [JohnnyVizz/claude-kit](https://github.com/JohnnyVizz/claude-kit) (MIT), adapted to local LLMs.
- LLM configuration shared with NEXUS.AI / DOINg.AI.
