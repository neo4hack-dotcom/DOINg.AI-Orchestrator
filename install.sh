#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v node >/dev/null || { echo "Node.js 20+ requis"; exit 1; }
command -v python3 >/dev/null || { echo "Python 3.10+ requis"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
(cd frontend && npm install --no-audit --no-fund && npm run build)
echo "OK. Lancez ./start.sh"
