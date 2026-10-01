#!/usr/bin/env bash
# Example end-to-end requests against a locally running backend.
# Start it first with:  ./run.sh
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "== health =="
curl -s "${BASE}/health"; echo

echo "== known sharp jump (tau = 10), IK bandwidth =="
.venv/bin/python - <<'PY' | curl -s -X POST "${BASE}/api/v1/rd/estimate" \
    -H "Content-Type: application/json" -d @- | .venv/bin/python -m json.tool
import json, numpy as np
rng = np.random.default_rng(7)
x = rng.uniform(-1, 1, 2000)
y = 1.5*x + 0.8*x**2 + np.where(x >= 0, 10.0, 0.0) + rng.normal(0, 1, x.size)
print(json.dumps({"x": x.tolist(), "y": y.tolist(), "cutoff": 0.0,
                  "kernel": "triangular", "bandwidth": "ik",
                  "se_type": "hc2", "run_id": "example-jump"}))
PY

echo
echo "== sparse boundary (hard gap) -> structured failed/non_identifiable =="
.venv/bin/python - <<'PY' | curl -s -X POST "${BASE}/api/v1/rd/estimate" \
    -H "Content-Type: application/json" -d @- | .venv/bin/python -m json.tool
import json, numpy as np
rng = np.random.default_rng(8)
half = 300
x = np.concatenate([rng.uniform(-1, -0.25, half), rng.uniform(0.25, 1, half)])
y = x + np.where(x >= 0, 4.0, 0.0) + rng.normal(0, 1, x.size)
print(json.dumps({"x": x.tolist(), "y": y.tolist(), "cutoff": 0.0,
                  "bandwidth": 0.1, "run_id": "example-sparse"}))
PY

echo
echo "== retrieve the successful run from SQLite =="
curl -s "${BASE}/api/v1/runs/example-jump" | .venv/bin/python -m json.tool | head -20
