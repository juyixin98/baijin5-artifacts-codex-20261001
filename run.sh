#!/usr/bin/env bash
# Start the motifscan API locally. Set up the environment first:
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")"
export MOTIFSCAN_DB_PATH="${MOTIFSCAN_DB_PATH:-motifscan.db}"
exec uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
