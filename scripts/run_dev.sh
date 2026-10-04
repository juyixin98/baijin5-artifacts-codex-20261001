#!/usr/bin/env bash
# 本地启动:建 venv、装锁定依赖、起服务
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet -r requirements.txt

# 私钥落盘加密密钥;不设置则进程内临时生成(重启后历史批次不可解密)
export PAGG_FERNET_KEY="${PAGG_FERNET_KEY:-MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=}"
export PAGG_DB_PATH="${PAGG_DB_PATH:-pagg.db}"
export PAGG_KEY_SIZE="${PAGG_KEY_SIZE:-2048}"

exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
