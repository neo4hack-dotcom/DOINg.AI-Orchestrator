# Security and privacy

DOINg.AI SuperCode is meant to run on your machine, with your local LLM, on code and data that must not leave it. This document lists what the app sends where, the protections in place, how they were checked, and what remains your responsibility.

## Where your data goes

The app has **no telemetry, no analytics, no crash reporting, no update check and no cloud service**. The UI loads nothing from the internet (no CDN, no web fonts; `img-src` / `connect-src` are limited to the app itself by its Content Security Policy).

| Destination | When | What is sent | Local-only mode |
| --- | --- | --- | --- |
| Your LLM (`Settings → Local LLM`) | every agent turn | prompts, the code the agents read, tool results (data results: the first rows only, see *Rows the agent sees*) | must be local / private network |
| MCP servers over HTTP / SSE | when an agent or the Data view calls a tool | the tool arguments | must be local / private network |
| MCP servers over stdio | same | the tool arguments, to a local process **you** configured | the process itself is not restricted (see residual risks) |
| `webfetch` tool | when an agent fetches a URL (asks in Ask mode) | the request | local / private network only; never the app's own API |
| Bitbucket | only when you open a pull request | the branch (git push) and the PR | the server must be local / private network |
| `npx rea-agents` (REA layer, off by default) | first use, after you enable it | package download from the npm registry | listed as an internet destination in Settings |
| Shell commands (yours and the agents') | when run | whatever the command does | network commands always ask, never run unattended in Auto |

`Settings → Privacy & security` lists the destinations of your current configuration, flags any that is not local, and shows the checks below.

## Protections

**Local-only mode** (`network.localOnly`, on by default) - `backend/netguard.py`
- Every HTTP client the backend creates (LLM, MCP HTTP / SSE, webfetch, Bitbucket) goes through `netguard.client()`, whose request hook checks **each request, redirects included**. Local means loopback, RFC 1918 / ULA private networks and `*.localhost`; the host name is resolved and every address must be local. Link-local addresses (`169.254.x.x`, cloud metadata endpoints) are not local. A blocked request raises `NetworkBlocked`, which is never retried.
- Shell commands are classified (`permissions.network_kind`): `curl`, `wget`, `ssh`, `scp`, `git push / pull / fetch / clone`, `nc`, `Invoke-WebRequest`… and package installs (`npm install`, `pip install`…, allowed by `network.allowPackageManagers`). With local-only on, they always ask, with the reason; in Auto mode they are refused.
- The preview only shows local addresses; the agents' `show` tool only accepts local URLs; `open` refuses internet URLs in local-only mode.

**HTTP server** - `backend/security.py`, `server.py`
- Binds `127.0.0.1` by default. Listening on any other address (`--host 0.0.0.0`) always requires an access token - `--token <secret>` / `ORCH_TOKEN`, or one generated and printed at start - on every API call (given once in the URL, then kept in an HttpOnly SameSite=Strict cookie, or sent in the `X-SuperCode-Token` header; compared in constant time).
- **Host allowlist**: requests whose `Host` header is not this machine (or the bound address, or `ORCH_ALLOWED_HOSTS`) are rejected - this defeats DNS rebinding.
- **CSRF**: `POST` / `PUT` / `PATCH` / `DELETE` from another origin (`Origin` or `Sec-Fetch-Site: cross-site`) are rejected. No CORS origin is allowed by default (the dev UI goes through Vite's proxy, which runs on loopback).
- **Content Security Policy** on the UI (`script-src 'self'`, no inline scripts, `connect-src 'self'`, `frame-src` limited to localhost for the preview, `frame-ancestors 'self'`); workspace files served for the preview carry a `sandbox` CSP (opaque origin: a project's HTML cannot call the API). Markdown from the LLM never loads remote images.

**What the agents can touch** - `backend/tools.py`, `backend/agent.py`
- Paths are resolved (symlinks followed) and must stay inside the session's project folder; the app's own data folder (`data/`: settings with secrets, sessions, mods, skills) is refused even when it sits inside the project; `.git` is read-only (a hook or `core.fsmonitor` would turn a file edit into code execution); walkers (`list`, `glob`, `grep`, the repo map) skip symlinks that point outside the project.
- `git` runs with `core.fsmonitor` disabled through environment config; the Bitbucket token is passed through the environment for one push, never written to git config.
- **Permission modes** (`ask` / `acceptEdits` / `auto`, global default and per session) decide what asks; deny rules, the plan agent's read-only restriction and local-only rules apply in every mode. Shell *allow* rules never cover a command with command substitution, redirections, background `&`, exec-style options (`find -exec`, `xargs`, `git -c`, `--output=`…), so `git status*` cannot be abused into running something else. Executables are never opened with the system (`open` tool); they go through `bash` and its rules.
- Data: SQL that may change data (anything but `SELECT` / `WITH` / `SHOW` / `EXPLAIN` / read `PRAGMA`; `SELECT … INTO` included) needs confirmation unless the session is in Auto mode. CSV / Excel / JSON files are loaded into an in-memory SQLite table and are read-only sources. On local SQLite, `ATTACH` and `VACUUM INTO` are refused in every mode (they would read or create database files outside the project), and queries stop after 30 s. CSV exports neutralize formulas (`=`, `+`, `-`, `@`) so a cell cannot run in Excel.

**Extensions**
- MCP servers declared by a project (`.mcp.json`) stay inert until approved; the approval is bound to a hash of the server's whole definition (command, args, env, URL, headers), so a modified `.mcp.json` needs a new approval.
- Mods: a mod folder is validated **statically** (syntax, `register`, declared events) - validating never runs it; user mods only load once installed / enabled; installing a mod written by the agent asks (`permissions.mods`).
- Secrets are masked before reaching the browser: the LLM key, the Bitbucket token, MCP env / headers values whose name looks secret, credentials in URLs (`user:pw@`, `?token=`), and secret-looking command-line arguments (`--api-key x`, `--token=x`). Masked values sent back keep the stored secret.

## How it was checked

- Code audit of every outbound path (search for HTTP clients, sockets, subprocesses, `urllib`) - all HTTP goes through `netguard.client()`.
- Tests (`python -m pytest`), among them `backend/tests/test_security.py` and `test_workspace.py`: local-only blocking of the LLM and webfetch, redirects, network shell commands per mode, Host / Origin / token guard, CSP headers, path traversal on the raw-file route, the data-folder fence, `ATTACH` / `VACUUM INTO` refused, `.git` read-only, symlinks skipped, MCP approval invalidation, static mod validation, secret masking, webfetch refusing the app's own port, the privacy report, SQL write confirmation, formula-safe CSV.
- CI runs them on Windows, macOS and Ubuntu.

## Residual risks (your responsibility)

- **Programs are not sandboxed by the OS.** Tests, builds and scripts that you or the agents run execute with your user's rights; a malicious repository can do anything its build scripts do. Keep **Ask** mode (the default) for code you do not trust, read commands before allowing them, and use a container or VM for untrusted projects.
- **Local processes are not network-filtered.** Local-only mode governs the app's own requests and classifies shell commands; it cannot stop a stdio MCP server, a mod (Python code you installed) or a program from opening a connection. Only install servers and mods you trust; a firewall rule is the way to enforce "nothing leaves" at the OS level.
- **Command classification is best effort.** A command can reach the network in ways the classifier does not recognize (a script that downloads something). In Ask mode every command asks anyway.
- **DNS check then connect.** netguard resolves the host before the request and httpx resolves it again to connect; a DNS server you do not control could answer differently the second time. Use IP addresses or a trusted resolver for LLM / MCP endpoints if this matters.
- **MCP `readOnlyHint` is trusted.** Tools a server marks read-only skip the prompt (per-server setting *trust read-only*); turn it off for servers you do not trust.
- **Preview of private-network apps**: only `localhost` can be embedded; other local addresses open in a browser tab.
- **LAN exposure**: with `--host 0.0.0.0`, anyone who has the token (it is in the printed URL and in the browser's cookie) can drive the agents, over plain HTTP. Prefer an SSH tunnel to the loopback port.

## Reporting a vulnerability

Please open a private security advisory on the repository (GitHub → Security → *Report a vulnerability*) rather than a public issue.
