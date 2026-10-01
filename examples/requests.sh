#!/usr/bin/env bash
# Local example requests against a running collate_agg server.
# Usage: ./examples/requests.sh [base_url]   (default http://127.0.0.1:8080)
set -u
BASE="${1:-http://127.0.0.1:8080}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== health =="
curl -s "$BASE/health"; echo

echo "== accent/case equivalence + NFD/NFC + numeric sequences (2026R1, accepted) =="
curl -s -X POST "$BASE/api/v1/group" \
  -H 'content-type: application/json' \
  --data-binary @"$HERE/equivalence.json"; echo

echo "== natural numeric order: a2/a02/A2 collapse; a10,a100 distinct; 3 classes =="
curl -s -X POST "$BASE/api/v1/group" \
  -H 'content-type: application/json' \
  --data-binary @"$HERE/numeric.json"; echo

echo "== rule-switch rejection: per-row mixed versions -> 409 rule_version_mixed =="
curl -s -w "\nHTTP %{http_code}\n" -X POST "$BASE/api/v1/group" \
  -H 'content-type: application/json' \
  --data-binary @"$HERE/mixed_versions.json"

echo "== unknown rule version -> 400 unknown_rule_version =="
curl -s -w "\nHTTP %{http_code}\n" -X POST "$BASE/api/v1/group" \
  -H 'content-type: application/json' \
  --data-binary @"$HERE/unknown_version.json"

echo "== 2026R2: case/accent sensitive -> more groups =="
curl -s -X POST "$BASE/api/v1/group" \
  -H 'content-type: application/json' \
  --data-binary @"$HERE/r2_sensitive.json"; echo
