#!/usr/bin/env bash
# Start the API locally (loopback only). Override via CRP_* env vars.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
exec python3 -m uvicorn commit_reveal.api.app_factory:app --host "${CRP_HOST:-127.0.0.1}" --port "${CRP_PORT:-8529}"
