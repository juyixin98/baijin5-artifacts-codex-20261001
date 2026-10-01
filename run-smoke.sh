#!/usr/bin/env bash
# End-to-end smoke: build offline, start the server, run one query, shut down.
set -euo pipefail
cargo run --offline --bin pctl-server -- config/pctl.toml &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do curl -fsS localhost:8080/health >/dev/null 2>&1 && break; sleep 0.2; done
curl -sS localhost:8080/query -H 'content-type: application/json' -d '{
  "group_by": "g",
  "columns": [
    {"name":"g","data_type":"utf8","values":["a","a","b","b","b"]},
    {"name":"v","data_type":"i64","values":[1,3,2,2,10]}
  ],
  "operators": [
    {"op":"percentile","column":"v","p":0.5,"method":"continuous"},
    {"op":"mode","column":"v"}
  ]
}' | python3 -m json.tool
