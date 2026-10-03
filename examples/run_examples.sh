#!/usr/bin/env bash
# Example requests against a locally running server (./run.sh first).
set -euo pipefail
cd "$(dirname "$0")"
BASE="${BASE:-http://127.0.0.1:8000}"

echo "== version =="
curl -s "$BASE/v1/version" | python3 -m json.tool

echo "== calibrate (exact score distribution + threshold) =="
curl -s -X POST "$BASE/v1/calibrate" -H 'content-type: application/json' \
  -d @calibrate_request.json | python3 -m json.tool

echo "== scan (hits + p-values + multiple-testing correction) =="
SCAN_RESPONSE=$(curl -s -X POST "$BASE/v1/scan" -H 'content-type: application/json' \
  -H 'x-request-id: example-run-1' -d @scan_request.json)
echo "$SCAN_RESPONSE" | python3 -m json.tool

echo "== provenance for the scan request =="
curl -s "$BASE/v1/requests/example-run-1" | python3 -m json.tool

echo "== declared failure: zero background probability =="
curl -s -X POST "$BASE/v1/scan" -H 'content-type: application/json' -d '{
  "sequences": [{"id": "s1", "sequence": "CAGT"}],
  "motif": {"name": "M2-demo", "matrix": [[4.0,0.0,0.0,0.0],[0.0,0.0,4.0,0.0]]},
  "background": {"A": 0.5, "C": 0.5, "G": 0.0, "T": 0.0}
}' | python3 -m json.tool
