#!/usr/bin/env bash
# 本地开发启动脚本。
# 用法: ./scripts/run_dev.sh [--reload]
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"
HOST="${REASONER__HOST:-127.0.0.1}"
PORT="${REASONER__PORT:-8000}"

echo "启动受限默认推理后端  http://${HOST}:${PORT}  (文档: /docs)"
exec python3 -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" "$@"
