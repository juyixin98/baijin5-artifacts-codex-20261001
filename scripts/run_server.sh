#!/usr/bin/env bash
# Start the API server locally.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8495
