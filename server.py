"""DOINg.AI SuperCode - start the API (and the built UI) on http://localhost:4001.

    python server.py [--port 4001] [--host 127.0.0.1] [--workspace PATH] [--token TOKEN]

By default the server only listens on this machine (127.0.0.1). To use it from another computer,
start it with --host 0.0.0.0: an access token is then required (generated if you do not give one)
and the URL to open, with the token, is printed.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="DOINg.AI SuperCode server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--workspace", help="Default project folder for new sessions")
    parser.add_argument("--open", action="store_true", help="Open the browser")
    parser.add_argument("--token", default=os.environ.get("ORCH_TOKEN", ""),
                        help="Access token required by the API (mandatory when listening beyond this machine)")
    args = parser.parse_args()

    loopback = args.host in ("localhost", "::1") or _is_loopback(args.host)
    token = args.token.strip()
    if not loopback and not token:
        token = secrets.token_urlsafe(24)
    os.environ["ORCH_BIND_HOST"] = args.host
    os.environ["ORCH_BIND_PORT"] = str(args.port)
    if token:
        os.environ["ORCH_TOKEN"] = token
    url = f"http://localhost:{args.port}" + (f"/?token={token}" if token else "")
    if not loopback:
        print(f"[supercode] Listening on {args.host}:{args.port} - anyone on your network who has the token can use it.")
        print(f"[supercode] Open: http://<this-machine>:{args.port}/?token={token}")

    if args.workspace:
        from backend.config import save_config
        from pathlib import Path

        save_config({"workspace": str(Path(args.workspace).expanduser().resolve())})
    if args.open:
        webbrowser.open(url)
    uvicorn.run("backend.main:app", host=args.host, port=args.port, log_level="info")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    main()
