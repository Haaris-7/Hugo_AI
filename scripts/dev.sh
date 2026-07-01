#!/usr/bin/env bash
#
# Run the Hugo backend (and frontend, if Node is available) locally for development.
# Backend: http://127.0.0.1:8000  ·  Cockpit: http://localhost:3000
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "Run ./scripts/bootstrap.sh first."; exit 1; }
[ -f .env ] || cp .env.example .env

pids=()
cleanup() { for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

echo "▶ Backend → http://127.0.0.1:8000  (docs at /docs)"
.venv/bin/python -m uvicorn hugo.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload &
pids+=($!)

if command -v npm >/dev/null && [ -d frontend/node_modules ]; then
  echo "▶ Cockpit → http://localhost:3000"
  ( cd frontend && npm run dev ) &
  pids+=($!)
else
  echo "⚠ Frontend not started (no node_modules). Use 'make up' for the dockerized cockpit."
fi

wait
