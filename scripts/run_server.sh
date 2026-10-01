#!/usr/bin/env bash
# Start the CUPED FastAPI service using the project config.
set -euo pipefail
cd "$(dirname "$0")/.."
export CUPED_CONFIG="${CUPED_CONFIG:-config/default.yaml}"
exec python3 -m uvicorn app.api.app:create_app --factory --host 127.0.0.1 --port "${PORT:-8000}"
