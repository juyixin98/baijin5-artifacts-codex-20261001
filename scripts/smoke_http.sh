#!/usr/bin/env bash
# Local HTTP smoke test against a server you start yourself:
#   IEJOIN_PORT=8090 cargo run --bin iejoin-server
#   ./scripts/smoke_http.sh 8090
#
# Asserts the exact five-pair answer, a 413 budget error and paging.
set -euo pipefail

PORT="${1:-8080}"
BASE="http://127.0.0.1:${PORT}"

req() { curl -s -o /tmp/iejoin_body -w "%{http_code}" "$@"; }

echo "== health =="
code=$(req "$BASE/healthz")
[ "$code" = "200" ] || { echo "health failed: $code"; exit 1; }

echo "== one-shot exact answer =="
code=$(req "$BASE/join" -H 'content-type: application/json' -d '{
  "plan": {"p1": {"left_col":0,"right_col":0,"op":"gt"},
           "p2": {"left_col":1,"right_col":1,"op":"lt"}},
  "left":  {"columns":[
    {"name":"a1","type":"int64","values":[5,2,5,9]},
    {"name":"a2","type":"int64","values":[7,7,1,3]}]},
  "right": {"columns":[
    {"name":"b1","type":"int64","values":[5,1,2,9]},
    {"name":"b2","type":"int64","values":[7,9,0,4]}]}
}')
[ "$code" = "200" ] || { echo "join failed: $code"; cat /tmp/iejoin_body; exit 1; }
grep -q '"emitted":5' /tmp/iejoin_body || { echo "expected 5 pairs"; cat /tmp/iejoin_body; exit 1; }

echo "== budget exceeded = 413 =="
code=$(req "$BASE/join" -H 'content-type: application/json' -d '{
  "plan": {"p1": {"left_col":0,"right_col":0,"op":"lt"},
           "p2": {"left_col":1,"right_col":1,"op":"lt"}},
  "left":  {"columns":[{"name":"a1","type":"int64","values":[1,2]},
                       {"name":"a2","type":"int64","values":[1,2]}]},
  "right": {"columns":[{"name":"b1","type":"int64","values":[3,4]},
                       {"name":"b2","type":"int64","values":[3,4]}]},
  "budget": {"max_output": 2}
}')
[ "$code" = "413" ] || { echo "expected 413, got $code"; cat /tmp/iejoin_body; exit 1; }

echo
echo "HTTP smoke checks passed."
