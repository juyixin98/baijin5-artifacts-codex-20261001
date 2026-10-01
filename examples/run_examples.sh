#!/usr/bin/env bash
# End-to-end walkthrough against a locally running server.
# Usage:
#   python -m app.main            # in one terminal
#   bash examples/run_examples.sh # in another
set -euo pipefail

export NO_PROXY='*'  # bypass any ambient proxy for localhost requests

BASE="${BASE:-http://127.0.0.1:8000}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

post() { curl -s -D /tmp/rct_headers -X POST "$BASE$1" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: walkthrough-001' \
  -d "$2"; echo; grep -i '^x-request-id' /tmp/rct_headers; }

echo "== health =="
curl -s "$BASE/health"; echo

echo "== exact p-value (expect 2/16 = 0.125) =="
post /api/v1/pvalue "$(cat "$HERE/pvalue_exact.json")"

echo "== exact 90% constant-effect set (expect interval [-4, 5]) =="
post /api/v1/inversion "$(cat "$HERE/inversion_exact.json")"

echo "== over-budget Monte-Carlo p-value (expect method + error fields) =="
post /api/v1/pvalue "$(cat "$HERE/pvalue_monte_carlo.json")"

echo "== deterministic replay (expect reproducible: true) =="
post /api/v1/replay "$(cat "$HERE/replay.json")"

echo "== persistence lookup by request id =="
curl -s "$BASE/api/v1/requests/walkthrough-001"; echo
