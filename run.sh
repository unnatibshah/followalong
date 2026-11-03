#!/usr/bin/env bash
# Start FollowAlong on http://localhost:${PORT:-8000}. Reads app/.env (MODEL_API_KEY, FA_*) when present.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
venv_py() { if [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; else echo .venv/bin/python; fi; }  # Windows | Unix
if [ ! -x "$(venv_py)" ]; then
  PY="$(command -v python3.12 || command -v python3 || command -v python)"
  "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { echo "Needs Python 3.12+ (or use: docker compose up)" >&2; exit 1; }
  "$PY" -m venv .venv
fi
PY="$(venv_py)"
"$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip >/dev/null  # venvs made by uv have no pip
"$PY" -m pip install -q -r requirements.txt
# --reload: fixtures and server code are re-read on change, so the UI never talks to a stale server while we build
exec "$PY" -m uvicorn server.main:app --host 127.0.0.1 --port "${PORT:-8000}" --no-access-log --reload --reload-dir server --reload-dir config --reload-include "*.toml" --reload-include "*.json" --reload-include "*.jsonl" --reload-include "*.md"
