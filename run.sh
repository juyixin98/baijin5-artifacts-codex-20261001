#!/usr/bin/env bash
# Local launcher for the RD backend. No external accounts needed.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip pip-tools >/dev/null
  .venv/bin/pip install -r requirements.txt
fi

export RD_DB_PATH="${RD_DB_PATH:-data/rd.db}"
export RD_LOG_FILE="${RD_LOG_FILE:-logs/rd.jsonl}"
export RD_HOST="${RD_HOST:-127.0.0.1}"
export RD_PORT="${RD_PORT:-8000}"

echo "Starting RD backend on http://${RD_HOST}:${RD_PORT} (docs at /docs)"
exec .venv/bin/uvicorn app.api.main:app --host "${RD_HOST}" --port "${RD_PORT}"
