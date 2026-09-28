#!/usr/bin/env bash
# Full verification: pinned environment check -> test suite -> replication.
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

echo "== environment =="
python3 --version
python3 - <<'PY'
import numpy, scipy, fastapi, pydantic, uvicorn, pytest, httpx
print("numpy", numpy.__version__)
print("scipy", scipy.__version__)
print("fastapi", fastapi.__version__)
print("pydantic", pydantic.__version__)
print("uvicorn", uvicorn.__version__)
print("pytest", pytest.__version__)
print("httpx", httpx.__version__)
PY

echo "== test suite (70 tests) =="
python3 -m pytest tests/ -q

echo "== replication experiment =="
mkdir -p artifacts
python3 scripts/run_experiment.py configs/experiment.json \
    artifacts/experiment_results.json artifacts/runs.db

echo "== smoke: HTTP API =="
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
AIPW_PORT="$PORT" AIPW_DB_PATH="artifacts/smoke_${PORT}.db" python3 scripts/serve.py >/tmp/aipw_smoke.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
    sleep 0.5
done
curl -fsS "http://127.0.0.1:${PORT}/health" && echo
rm -f "artifacts/smoke_${PORT}.db"
echo "verification complete"
