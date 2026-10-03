#!/usr/bin/env bash
# Local development launcher for the STFT backend.
# Usage: ./scripts/run_dev.sh [host] [port]
set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-8000}"

cd "$(dirname "$0")/.."

export STFT_LOG_LEVEL="${STFT_LOG_LEVEL:-INFO}"
export STFT_DEFAULT_NPERSEG="${STFT_DEFAULT_NPERSEG:-256}"
export STFT_DEFAULT_HOP="${STFT_DEFAULT_HOP:-128}"

exec python3 -m uvicorn stft_backend.api:app --host "$HOST" --port "$PORT" --reload
