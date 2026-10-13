"""Pull requests to Bitbucket Server / Data Center (self-hosted, enterprise) and Bitbucket Cloud.

Flow: (new branch) -> git add / commit -> git push -> REST "create pull request".

- The project/workspace and repository slug come from the settings, or are parsed from
  the git remote (``https://host/scm/PROJ/repo.git``, ``ssh://git@host:7999/proj/repo.git``,
  ``https://bitbucket.org/ws/repo.git``, ``git@bitbucket.org:ws/repo.git``).
- Push uses the user's git credentials (credential manager, SSH key). For HTTPS remotes the
  configured token is also passed as an ``http.extraHeader`` so a fresh machine works without
  a credential prompt (never written to disk or to the git config).
- Server API: ``POST /rest/api/1.0/projects/{key}/repos/{slug}/pull-requests``;
  Cloud API: ``POST /2.0/repositories/{workspace}/{slug}/pullrequests``.
"""
from __future__ import annotations

import asyncio
import base64
import re
import subprocess
import time
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from . import netguard
from .osutil import IS_WINDOWS, git_safety_env

NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}


class BitbucketError(Exception):
    pass


# ---------------------------------------------------------------------------
# git


async def git(directory: str, *args: str, stdin: Optional[bytes] = None, timeout: int = 180,
              config: Optional[dict] = None) -> tuple[int, str]:
    """``config``: extra git settings passed through the environment (secrets never appear in the command line)."""
    def run() -> tuple[int, str]:
        import os

        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C"}
        env.update(git_safety_env(env))
        n = int(env["GIT_CONFIG_COUNT"])
        for i, (k, v) in enumerate((config or {}).items(), start=n):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
            env["GIT_CONFIG_COUNT"] = str(i + 1)
        res = subprocess.run(["git", *args], cwd=directory, input=stdin, capture_output=True, timeout=timeout,
                             env=env, **NO_WINDOW)
        return res.returncode, (res.stdout + res.stderr).decode("utf-8", "replace").strip()

    try:
        return await asyncio.to_thread(run)
    except FileNotFoundError as e:
        raise BitbucketError("git is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise BitbucketError(f"git {' '.join(args[:2])} timed out") from e


def _remote_host(url: str) -> str:
    """Host of an https:// or ssh (git@host:path, ssh://) git remote; "" for a local path (/srv/repo.git,
    C:/repo.git, C:\\repo.git, ./repo, file://) - git reads a drive letter as a path, not as an scp-like host."""
    url = (url or "").strip()
    if re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith(("/", "\\", ".", "~", "file:")):
        return ""
    m = re.match(r"^[\w+.-]+://(?:[^@/]+@)?([^/:]+)", url) or re.match(r"^(?:[^@/]+@)?([^:/\\]+):", url)
    return m.group(1) if m else ""


def parse_remote(url: str) -> Optional[dict]:
    """{"kind": "server"|"cloud", "host", "project", "repo", "baseUrl"} from a git remote URL."""
    url = (url or "").strip()
    if not url:
        return None
    m = re.match(r"^[\w.-]+@([\w.-]+):(?:\d+/)?([^/]+)/([^/]+?)(?:\.git)?/?$", url)  # scp-like ssh
    if m:
        host, owner, repo = m.groups()
        kind = "cloud" if host == "bitbucket.org" else "server"
        return {"kind": kind, "host": host, "project": owner, "repo": repo, "baseUrl": f"https://{host}" if kind == "server" else ""}
    p = urlparse(url)
    if not p.hostname:
        return None
    parts = [x for x in p.path.split("/") if x]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    host = p.hostname
    if host == "bitbucket.org":
        if len(parts) >= 2:
            return {"kind": "cloud", "host": host, "project": parts[0], "repo": parts[1], "baseUrl": ""}
        return None
    if "scm" in parts:  # https://host[/context]/scm/PROJ/repo
        i = parts.index("scm")
        if len(parts) >= i + 3:
            context = "/".join(parts[:i])
            port = f":{p.port}" if p.port and p.scheme in ("http", "https") else ""
            base = f"{p.scheme if p.scheme in ('http', 'https') else 'https'}://{host}{port}" + (f"/{context}" if context else "")
            return {"kind": "server", "host": host, "project": parts[i + 1], "repo": parts[i + 2], "baseUrl": base}
    if "projects" in parts and "repos" in parts:  # a browser URL pasted as remote
        i, j = parts.index("projects"), parts.index("repos")
        if len(parts) > max(i + 1, j + 1):
            return {"kind": "server", "host": host, "project": parts[i + 1], "repo": parts[j + 1], "baseUrl": f"https://{host}"}
    if len(parts) >= 2:  # ssh://git@host:7999/proj/repo.git
        return {"kind": "server", "host": host, "project": parts[-2], "repo": parts[-1], "baseUrl": f"https://{host}"}
    return None


async def status(directory: str, remote: str = "origin") -> dict:
    code, _ = await git(directory, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        return {"isRepo": False}
    code, branch = await git(directory, "symbolic-ref", "--short", "HEAD")  # works on a repo without commits
    if code != 0:
        _, branch = await git(directory, "rev-parse", "--short", "HEAD")  # detached HEAD
    _, url = await git(directory, "remote", "get-url", remote)
    _, porcelain = await git(directory, "status", "--porcelain")
    _, branches = await git(directory, "branch", "--format=%(refname:short)")
    has_commits = (await git(directory, "rev-parse", "--verify", "HEAD"))[0] == 0
    remote_ok = bool(url) and not url.startswith(("error", "fatal"))
    return {
        "isRepo": True,
        "branch": branch or "main",
        "hasCommits": has_commits,
        "remoteUrl": url if remote_ok else "",
        "remote": parse_remote(url) if remote_ok else None,
        "changes": [l for l in porcelain.splitlines() if l.strip()],
        "branches": [b for b in branches.splitlines() if b.strip()],
    }


# ---------------------------------------------------------------------------
# REST


def resolve_target(cfg: dict, st: dict) -> dict:
    """Merge settings with what the remote tells us."""
    remote = st.get("remote") or {}
    kind = cfg.get("kind") or remote.get("kind") or "server"
    if remote.get("kind") and not cfg.get("baseUrl") and not cfg.get("project"):
        kind = remote["kind"]
    project = cfg.get("project") or remote.get("project") or ""
    repo = cfg.get("repo") or remote.get("repo") or ""
    if kind == "server":
        base = (cfg.get("baseUrl") or remote.get("baseUrl") or "").rstrip("/")
        api = f"{base}/rest/api/1.0" if base else ""
    else:
        base = "https://bitbucket.org"
        api = (cfg.get("baseUrl") or "https://api.bitbucket.org/2.0").rstrip("/")
    if not api:
        raise BitbucketError("Bitbucket URL missing: set it in Settings > Bitbucket (e.g. https://bitbucket.company.com).")
    if not project or not repo:
        raise BitbucketError("Bitbucket project and repository unknown: add a Bitbucket remote or set them in Settings > Bitbucket.")
    return {"kind": kind, "api": api, "web": base, "project": project, "repo": repo}


def _auth_header(cfg: dict) -> dict:
    token = cfg.get("token") or ""
    if not token:
        return {}
    if cfg.get("username"):
        raw = base64.b64encode(f"{cfg['username']}:{token}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}
    return {"Authorization": f"Bearer {token}"}


def _repo_url(t: dict) -> str:
    if t["kind"] == "server":
        return f"{t['api']}/projects/{quote(t['project'])}/repos/{quote(t['repo'])}"
    return f"{t['api']}/repositories/{quote(t['project'])}/{quote(t['repo'])}"


def _client(cfg: dict) -> httpx.AsyncClient:
    return netguard.client(timeout=30, verify=bool(cfg.get("verifySsl", True)),
                             headers={"Accept": "application/json", **_auth_header(cfg)})


def _error(res: httpx.Response) -> str:
    try:
        data = res.json()
    except ValueError:
        return f"HTTP {res.status_code}: {res.text[:300]}"
    if isinstance(data, dict):
        if data.get("errors"):
            return f"HTTP {res.status_code}: " + "; ".join(str(e.get("message", e)) for e in data["errors"])
        if isinstance(data.get("error"), dict):
            return f"HTTP {res.status_code}: {data['error'].get('message', data['error'])}"
    return f"HTTP {res.status_code}: {str(data)[:300]}"


async def test(cfg: dict, directory: str) -> dict:
    st = await status(directory, cfg.get("remote") or "origin") if directory else {}
    t = resolve_target(cfg, st)
    async with _client(cfg) as client:
        res = await client.get(_repo_url(t))
    if res.status_code >= 400:
        raise BitbucketError(_error(res))
    data = res.json()
    return {"ok": True, "kind": t["kind"], "project": t["project"], "repo": t["repo"],
            "name": data.get("name") or data.get("full_name") or t["repo"]}


async def default_branch(cfg: dict, t: dict) -> str:
    async with _client(cfg) as client:
        if t["kind"] == "server":
            res = await client.get(f"{_repo_url(t)}/default-branch")
            if res.status_code < 400:
                return res.json().get("displayId") or "main"
        else:
            res = await client.get(_repo_url(t))
            if res.status_code < 400:
                return ((res.json().get("mainbranch") or {}).get("name")) or "main"
    return "main"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "changes"


async def create_pull_request(directory: str, cfg: dict, *, title: str, description: str = "", branch: str = "",
                              target: str = "", commit_message: str = "") -> dict:
    remote_name = cfg.get("remote") or "origin"
    st = await status(directory, remote_name)
    if not st.get("isRepo"):
        raise BitbucketError("This project is not a git repository.")
    if not st.get("remoteUrl"):
        raise BitbucketError(f"No git remote '{remote_name}': add your Bitbucket repository (git remote add {remote_name} <url>).")
    remote_host = _remote_host(st["remoteUrl"])
    if netguard.local_only() and remote_host and not netguard.host_is_local(remote_host):
        raise BitbucketError(f"Local-only mode: the git remote {remote_host} is not on your machine or private network. "
                             "Turn local-only mode off in Settings > Privacy & security to push there.")
    t = resolve_target(cfg, st)
    target = target or cfg.get("targetBranch") or await default_branch(cfg, t)
    steps: list[str] = []

    current = st["branch"]
    branch = (branch or "").strip()
    if not branch:
        shared = current in (target, "HEAD", "", "main", "master", "develop", "trunk") or current.startswith(("release/", "hotfix/"))
        branch = f"{cfg.get('branchPrefix') or 'supercode/'}{_slug(title)}-{time.strftime('%Y%m%d-%H%M')}" if shared else current
    if branch == target:
        raise BitbucketError(f"Source and target branch are both '{target}': choose another branch name.")
    if branch != current:
        exists = branch in st.get("branches", [])
        code, out = await git(directory, "checkout", branch) if exists else await git(directory, "checkout", "-b", branch)
        if code != 0:
            raise BitbucketError(f"git checkout {branch} failed: {out[:300]}")
        steps.append(f"branch {branch}")

    if st.get("changes"):
        await git(directory, "add", "-A")
        msg = (commit_message or title).strip() + "\n\nCreated with DOINg.AI SuperCode"
        code, out = await git(directory, "commit", "-q", "-F", "-", stdin=msg.encode("utf-8"))
        if code != 0:
            raise BitbucketError(f"git commit failed: {out[:300]}")
        steps.append("commit")

    push_args = ["push", "-u", remote_name, f"HEAD:refs/heads/{branch}"]
    header = _auth_header(cfg)
    extra = {"http.extraHeader": f"Authorization: {header['Authorization']}"} if header and st["remoteUrl"].startswith("http") else None
    code, out = await git(directory, *push_args, timeout=600, config=extra)
    if code != 0:
        raise BitbucketError(f"git push failed: {out[-500:]}")
    steps.append("push")

    if t["kind"] == "server":
        repo_ref = {"slug": t["repo"], "project": {"key": t["project"]}}
        body = {
            "title": title, "description": description, "state": "OPEN", "open": True, "closed": False,
            "fromRef": {"id": f"refs/heads/{branch}", "repository": repo_ref},
            "toRef": {"id": f"refs/heads/{target}", "repository": repo_ref},
            "reviewers": [{"user": {"name": r}} for r in cfg.get("reviewers", [])],
        }
        url = f"{_repo_url(t)}/pull-requests"
    else:
        body = {
            "title": title, "description": description, "close_source_branch": True,
            "source": {"branch": {"name": branch}}, "destination": {"branch": {"name": target}},
            "reviewers": [{"uuid": r} if r.startswith("{") else {"account_id": r} for r in cfg.get("reviewers", [])],
        }
        url = f"{_repo_url(t)}/pullrequests"

    async with _client(cfg) as client:
        res = await client.post(url, json=body)
        if res.status_code == 409 and t["kind"] == "server":
            existing = await client.get(url, params={"at": f"refs/heads/{branch}", "direction": "OUTGOING", "state": "OPEN"})
            values = existing.json().get("values", []) if existing.status_code < 400 else []
            if values:
                pr = values[0]
                return {"id": pr["id"], "url": _pr_link(pr, t), "branch": branch, "target": target, "steps": steps + ["existing PR"],
                        "existing": True}
        if res.status_code >= 400:
            raise BitbucketError(f"Pushed {branch}, but the PR could not be created: {_error(res)}")
        pr = res.json()
    return {"id": pr.get("id"), "url": _pr_link(pr, t), "branch": branch, "target": target, "steps": steps + ["pull request"]}


def _pr_link(pr: dict, t: dict) -> str:
    links = pr.get("links") or {}
    if isinstance(links.get("self"), list) and links["self"]:
        return links["self"][0].get("href", "")
    if isinstance(links.get("html"), dict):
        return links["html"].get("href", "")
    if t["kind"] == "server":
        return f"{t['web']}/projects/{t['project']}/repos/{t['repo']}/pull-requests/{pr.get('id')}"
    return f"https://bitbucket.org/{t['project']}/{t['repo']}/pull-requests/{pr.get('id')}"


def draft_description(session_data: dict, diffstat: str) -> dict:
    """Title and Markdown description built from the session (no LLM call, no tokens)."""
    flow = session_data.get("flow") or {}
    title = (flow.get("title") if flow and flow.get("title") != "savvy-flow" else "") or session_data.get("title") or "Changes"
    lines = []
    if flow:
        if flow.get("summary"):
            lines += ["## Summary", "", flow["summary"], ""]
        tasks = flow.get("tasks") or []
        if tasks:
            lines += ["## Tasks", ""]
            for t in tasks:
                mark = {"accepted": "x", "failed": "!", "blocked": "!"}.get(t.get("status"), " ")
                check = ""
                if t.get("checks"):
                    last = t["checks"][-1]
                    check = f" - check `{last['command']}` {'passed' if last['exitCode'] == 0 else 'failed'}"
                lines.append(f"- [{mark}] {t['n']}. {t['title']} ({t.get('tier')}){check}")
            lines.append("")
        if flow.get("integration"):
            lines += ["## Verification", ""] + [f"- `{c['command']}` -> exit {c['exitCode']}" for c in flow["integration"]] + [""]
    else:
        users = [p.get("text", "") for m in session_data.get("messages", []) if m["role"] == "user" and not m.get("reverted")
                 for p in m["parts"] if p.get("type") == "text"]
        if users:
            lines += ["## Requests", ""] + [f"- {u.strip()[:200]}" for u in users[-10:]] + [""]
    if diffstat:
        lines += ["## Files", "", "```", diffstat[-3000:], "```", ""]
    lines.append("_Created with DOINg.AI SuperCode._")
    return {"title": title[:120], "description": "\n".join(lines)}
