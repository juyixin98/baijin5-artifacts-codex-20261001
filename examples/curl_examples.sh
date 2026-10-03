#!/usr/bin/env bash
# curl examples against a locally running server (bash scripts/run_server.sh).
set -euo pipefail
BASE=http://127.0.0.1:8495

echo "== health =="
curl -s "$BASE/health" | python3 -m json.tool

echo "== estimate (integer_shift fixture) =="
python3 - <<'EOF' > /tmp/estimate_payload.json
import base64, json, pathlib
fx = pathlib.Path("fixtures")
print(json.dumps({
    "image_a": base64.b64encode((fx / "integer_shift_a.png").read_bytes()).decode(),
    "image_b": base64.b64encode((fx / "integer_shift_b.png").read_bytes()).decode(),
}))
EOF
curl -s -X POST "$BASE/v1/estimate" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-0001" \
  -d @/tmp/estimate_payload.json | python3 -m json.tool

echo "== fixtures listing =="
curl -s "$BASE/v1/fixtures" | python3 -m json.tool

echo "== validation run =="
curl -s -X POST "$BASE/v1/validation/run" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['report']['summary'], indent=2))"
