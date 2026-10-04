#!/usr/bin/env bash
# Local development launcher for the digest service.
set -euo pipefail
cd "$(dirname "$0")"

export DIGEST_HOST="${DIGEST_HOST:-127.0.0.1}"
export DIGEST_PORT="${DIGEST_PORT:-8000}"

mkdir -p data logs
exec python3 -m uvicorn app.api.main:app \
  --host "${DIGEST_HOST}" --port "${DIGEST_PORT}" "$@"
