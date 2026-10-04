#!/usr/bin/env bash
# Run the loopback-only SCRAM-SHA-256 test server.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi
export SCRAM_CONFIG="${SCRAM_CONFIG:-config/config.toml}"
exec ./.venv/bin/python -m scram_auth.main
