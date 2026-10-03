#!/usr/bin/env bash
# Run the complete test suite (unit + numerical + integration).
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m pytest -v --cov=stft_backend --cov-report=term-missing "$@"
