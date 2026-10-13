#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && . .venv/bin/activate
echo "DOINg.AI SuperCode on http://localhost:4001"
exec python3 server.py "$@"
