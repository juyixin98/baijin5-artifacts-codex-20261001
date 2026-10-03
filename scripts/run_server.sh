#!/usr/bin/env bash
# Start the validation API against the local synthetic fixtures.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m uvicorn msa_backend.api.app:app --host 127.0.0.1 --port 8000 "$@"
