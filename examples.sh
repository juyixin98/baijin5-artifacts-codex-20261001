#!/usr/bin/env bash
# Example requests against a locally running digest service.
# Usage: ./examples.sh   (after: ./run.sh)
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "== health =="
curl -s "${BASE}/health"; echo

echo "== metadata (mass table, versions, constraints) =="
curl -s "${BASE}/api/v1/meta" | python3 -m json.tool; echo

echo "== enzyme catalog =="
curl -s "${BASE}/api/v1/enzymes" | python3 -m json.tool; echo

echo "== complete trypsin digest: AAKRPA (cut bond 3, bond 4 blocked by P) =="
curl -s -X POST "${BASE}/api/v1/digest" \
  -H 'content-type: application/json' \
  -d '{"sequence":"AAKRPA","enzyme":"trypsin_syn","missed_cleavages":0,"run_id":"example-1"}' \
  | python3 -m json.tool; echo

echo "== one missed cleavage: AAKFAKLA -> AAK | FAK | LA =="
curl -s -X POST "${BASE}/api/v1/digest" \
  -H 'content-type: application/json' \
  -d '{"sequence":"AAKFAKLA","enzyme":"trypsin_no_proline_rule","missed_cleavages":1}' \
  | python3 -m json.tool; echo

echo "== unknown residue X: request succeeds, fragment mass status is UNKNOWN =="
curl -s -X POST "${BASE}/api/v1/digest" \
  -H 'content-type: application/json' \
  -d '{"sequence":"AAKXAA","enzyme":"trypsin_syn","missed_cleavages":0}' \
  | python3 -m json.tool; echo

echo "== ambiguous residue B: bounded mass range =="
curl -s -X POST "${BASE}/api/v1/digest" \
  -H 'content-type: application/json' \
  -d '{"sequence":"AKBAA","enzyme":"trypsin_no_proline_rule","missed_cleavages":0}' \
  | python3 -m json.tool; echo

echo "== categorized failure: illegal symbol (whitespace) =="
curl -s -X POST "${BASE}/api/v1/digest" \
  -H 'content-type: application/json' \
  -d '{"sequence":"AA K","enzyme":"trypsin_syn","missed_cleavages":0}' \
  | python3 -m json.tool; echo

echo "== provenance lookup of the first run =="
curl -s "${BASE}/api/v1/runs/example-1" | python3 -m json.tool; echo
