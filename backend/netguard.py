"""Local-only mode: the app itself never talks to the internet.

When ``network.localOnly`` is on (the default), every HTTP request the backend makes - the
LLM, the webfetch tool, MCP HTTP / SSE servers, the Bitbucket API - must go to a local
destination: ``localhost``, a loopback / private (RFC 1918, ULA) / link-local address, or a
host name that resolves only to such addresses (an intranet server). The check runs on
every request, redirects included, through an httpx event hook.

What it cannot see: programs the agents run (``npm install``, ``git push``...). Those go
through the shell permission rules; see ``permissions.network_kind``.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx

_cache: dict[str, tuple[float, bool]] = {}
CACHE_SECONDS = 60


class NetworkBlocked(httpx.HTTPError):
    """Raised for a non-local destination while local-only mode is on (never retried)."""


def local_only(cfg: Optional[dict] = None) -> bool:
    if cfg is None:
        from .config import load_config

        cfg = load_config()
    return bool((cfg.get("network") or {}).get("localOnly", True))


def _ip_is_local(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # Link-local (169.254.x - cloud metadata endpoints) is deliberately excluded.
    return bool(ip.is_loopback or (ip.is_private and not ip.is_link_local)) and not ip.is_multicast and not ip.is_unspecified


def host_is_local(host: str) -> bool:
    """True for localhost, loopback / private / link-local IPs, and names resolving only to them."""
    host = (host or "").strip().strip("[]").rstrip(".").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return _ip_is_local(ipaddress.ip_address(host))
    except ValueError:
        pass
    cached = _cache.get(host)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = {i[4][0].split("%")[0] for i in infos}
        ok = bool(addrs) and all(_ip_is_local(ipaddress.ip_address(a)) for a in addrs)
    except (OSError, ValueError):
        ok = False  # unresolvable: cannot prove it is local
    _cache[host] = (time.monotonic(), ok)
    return ok


def url_is_local(url: str) -> bool:
    try:
        return host_is_local(urlsplit(url).hostname or "")
    except ValueError:
        return False


def check_url(url: str, purpose: str = "request") -> None:
    """Raise NetworkBlocked when local-only mode forbids this URL."""
    if local_only() and not url_is_local(url):
        raise NetworkBlocked(
            f"Local-only mode blocked a {purpose} to {urlsplit(url).hostname or url}: it is not a local or private-network "
            "address. Use a local endpoint, or turn local-only mode off in Settings > Privacy & security.")


async def _hook(request: httpx.Request) -> None:
    if not local_only():
        return
    host = request.url.host
    ok = await asyncio.to_thread(host_is_local, host)
    if not ok:
        raise NetworkBlocked(
            f"Local-only mode blocked a request to {host}: it is not a local or private-network address. "
            "Turn local-only mode off in Settings > Privacy & security to allow internet destinations.")


def client(**kwargs) -> httpx.AsyncClient:
    """An httpx client whose every request (redirects included) obeys local-only mode."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks = {**hooks, "request": [_hook, *hooks.get("request", [])]}
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)


def describe(cfg: dict) -> list[dict]:
    """Every outbound destination the app is configured to reach, for Settings > Privacy & security."""
    out: list[dict] = []

    def add(kind: str, name: str, url: str, note: str = "") -> None:
        local = url_is_local(url) if url else True
        out.append({"kind": kind, "name": name, "url": url, "local": local,
                    "blocked": bool(url) and not local and local_only(cfg), "note": note})

    llm = cfg.get("llm") or {}
    add("llm", f"LLM ({llm.get('provider')})", llm.get("baseUrl") or "")
    if ((cfg.get("permissions") or {}).get("webfetch") or "ask") != "deny":
        out.append({"kind": "webfetch", "name": "webfetch tool (agents)", "url": "", "local": local_only(cfg), "blocked": False,
                    "note": "local and private-network URLs only" if local_only(cfg) else "any URL the agents decide to fetch"})
    for name, srv in ((cfg.get("mcp") or {}).get("servers") or {}).items():
        if srv.get("url") and srv.get("enabled", True):
            add("mcp", f"MCP server {name}", srv["url"])
        elif srv.get("command") and srv.get("enabled", True):
            out.append({"kind": "mcp", "name": f"MCP server {name}", "url": "", "local": True, "blocked": False,
                        "note": f"local process ({srv['command']}); what it does with the network is up to the server"})
    bb = cfg.get("bitbucket") or {}
    if bb.get("token"):
        add("bitbucket", f"Bitbucket {bb.get('kind')}", "https://api.bitbucket.org" if bb.get("kind") == "cloud" else bb.get("baseUrl") or "",
            "only when you open a pull request")
    rev = cfg.get("reverse") or {}
    if rev.get("enabled"):
        out.append({"kind": "reverse", "name": "REA (npx rea-agents)", "url": "https://registry.npmjs.org", "local": False, "blocked": False,
                    "note": "npx downloads the package from the npm registry on first use; REA then analyzes locally"})
    return out
