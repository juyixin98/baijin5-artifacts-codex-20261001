#!/usr/bin/env bash
# Local smoke examples - no external accounts needed.
# Usage: ./examples/curl_examples.sh   (server must be running on :8000)
set -euo pipefail

BASE="${NUSSINOV_BASE_URL:-http://127.0.0.1:8000}"

echo "== health =="
curl -sS "$BASE/health" | python3 -m json.tool

echo
echo "== hairpin GGGAAACCC (optimum 3, deterministic primary) =="
curl -sS -X POST "$BASE/api/v1/fold" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-hairpin-001' \
  --data @examples/request_hairpin.json | python3 -m json.tool

echo
echo "== multiple optima GGAUCC (enumerate all 3) =="
curl -sS -X POST "$BASE/api/v1/fold" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-multiopt-001' \
  --data @examples/request_multiple_optima.json | python3 -m json.tool

echo
echo "== lineage retrieval for demo-hairpin-001 =="
curl -sS "$BASE/api/v1/lineage/demo-hairpin-001" | python3 -m json.tool

echo
echo "== invalid base failure category =="
curl -sS -X POST "$BASE/api/v1/fold" \
  -H 'Content-Type: application/json' \
  --data @examples/request_invalid_base.json | python3 -m json.tool
