#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v node >/dev/null || { echo "Node.js 20+ is required"; exit 1; }
command -v python3 >/dev/null || { echo "Python 3.10+ is required"; exit 1; }
command -v git >/dev/null || echo "Warning: git not found (needed for undo/snapshots and pull requests)"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
(cd frontend && npm install --no-audit --no-fund && npm run build)
echo "Done. Run ./start.sh"
