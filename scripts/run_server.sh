#!/usr/bin/env bash
# Start the TensorCraft API locally.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${TENSORCRAFT_HOST:-127.0.0.1}"
PORT="${TENSORCRAFT_PORT:-8000}"

exec python3 -m uvicorn tensorcraft.main:app --host "$HOST" --port "$PORT"
