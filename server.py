"""DOINg.AI Orchestrator - start the API (and the built UI) on http://localhost:4001.

    python server.py [--port 4001] [--host 127.0.0.1] [--workspace PATH]
"""
from __future__ import annotations

import argparse
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="DOINg.AI Orchestrator server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--workspace", help="Default project folder for new sessions")
    parser.add_argument("--open", action="store_true", help="Open the browser")
    args = parser.parse_args()

    if args.workspace:
        from backend.config import save_config
        from pathlib import Path

        save_config({"workspace": str(Path(args.workspace).expanduser().resolve())})
    if args.open:
        webbrowser.open(f"http://localhost:{args.port}")
    uvicorn.run("backend.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
