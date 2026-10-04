#!/usr/bin/env bash
# Start the local Paillier aggregation test service.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PAILLIER_DB_PATH:=paillier_service.db}"
: "${PAILLIER_KEY_SIZE:=2048}"
: "${HOST:=127.0.0.1}"
: "${PORT:=8000}"
export PAILLIER_DB_PATH PAILLIER_KEY_SIZE

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
