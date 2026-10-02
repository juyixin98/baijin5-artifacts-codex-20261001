#!/usr/bin/env bash
# Local, offline demo for pull-query. Builds the service, starts it on an
# ephemeral port, exercises scan / spill-sort / timeout / user-cancel, and
# prints a verdict. Requires only the Rust toolchain; no accounts or network.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PQ_PORT:-8099}"
BASE="http://127.0.0.1:${PORT}"

echo ">> building (release)…"
cargo build --release --quiet

echo ">> starting pq-server on ${BASE}"
PQ_PORT="${PORT}" ./target/release/pq-server >/tmp/pq-demo.log 2>&1 &
PID=$!
trap 'kill ${PID} 2>/dev/null || true' EXIT
sleep 1

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

say "health"
curl -s "${BASE}/health"; echo

say "scan users (typed batches)"
curl -s -X POST "${BASE}/query" \
  -H 'content-type: application/json' \
  -d '{"source":{"table":"users"},"batch_size":3}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("ok=",d["ok"],"rows=",d["stats"]["rows_out"],"run_id=",d["run_id"])'

say "blocking spill-sort of 50 orders (budget forces 10+ runs)"
curl -s -X POST "${BASE}/query" \
  -H 'content-type: application/json' \
  -d '{"source":{"table":"orders","rows":50},"batch_size":6,"sort_keys":["amount"],"sort_memory_budget_bytes":120}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);s=d["stats"];print("ok=",d["ok"],"rows=",s["rows_out"],"runs_spilled=",s["runs_spilled"],"open_files_after_close=",s["open_files_after_close"],"buffered_after_close=",s["buffered_bytes_after_close"])'

say "early downstream stop (limit) reclaims upstream"
curl -s -X POST "${BASE}/query" \
  -H 'content-type: application/json' \
  -d '{"source":{"table":"orders","rows":100},"batch_size":5,"limit":7}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("ok=",d["ok"],"rows=",d["stats"]["rows_out"])'

say "timeout is distinct from cancellation (expect HTTP 408 kind=timeout)"
curl -s -o /tmp/t.json -w "http_status=%{http_code}\n" -X POST "${BASE}/validate/timeout"
python3 -c 'import json;d=json.load(open("/tmp/t.json"));print("kind=",d["error"]["kind"])'

say "user cancellation race (expect HTTP 409 kind=cancelled)"
curl -s -o /tmp/c.json -w "http_status=%{http_code}\n" -X POST "${BASE}/validate/cancel"
python3 -c 'import json;d=json.load(open("/tmp/c.json"));print("kind=",d["error"]["kind"],"open_files_after_close=",d["resources"]["open_files_after_close"])'

say "stream endpoint cancelled mid-flight (NDJSON, last event is stats)"
curl -s "${BASE}/query/stream?batches=50&delay_ms=20&cancel_after_ms=40" | tail -1

echo
echo ">> demo complete; server log: /tmp/pq-demo.log"
