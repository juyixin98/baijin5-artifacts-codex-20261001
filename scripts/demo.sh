#!/usr/bin/env bash
# pullq local demo: sort, join, timeout, user-cancel, and leak check.
# Every step prints the run id and the error category involved, so the
# output can be replayed against the README's error-semantics table.
set -euo pipefail

PORT="${PORT:-8173}"
BASE="http://127.0.0.1:${PORT}"
SPILL_DIR="$(mktemp -d)/pullq-spill"
SERVER_LOG="$(mktemp -d)/pullq-server.log"

cleanup() {
  [[ -n "${SERVER_PID:-}" ]] && kill "${SERVER_PID}" 2>/dev/null || true
}
trap cleanup EXIT

echo "== building pullq =="
cargo build --quiet

echo "== starting server (port ${PORT}, spill dir ${SPILL_DIR}) =="
PORT="${PORT}" SPILL_DIR="${SPILL_DIR}" ./target/debug/pullq >"${SERVER_LOG}" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 50); do
  curl -sf "${BASE}/health" >/dev/null 2>&1 && break
  sleep 0.1
done
echo "health: $(curl -s "${BASE}/health")"

step() { echo; echo "== $1 =="; }

step "1. blocking sort over scan (8 batches x 256 rows, tiny memory -> spills)"
curl -s -X POST "${BASE}/query" -H 'content-type: application/json' -d '{
  "plan": {"op":"sort","key":"k","run_rows":256,
           "input":{"op":"scan","table":"numbers","batches":8,"batch_rows":256,"seed":42}},
  "timeout_ms": 10000, "memory_limit_bytes": 8192
}' | jq '{run_id, status, row_count, first_keys: [.rows[0:8][][0]],
          sorted: ([.rows[][0]] == ([.rows[][0]] | sort)),
          metrics: {memory_used_bytes: .metrics.memory_used_bytes,
                    spill_files_created_total: .metrics.spill_files_created_total,
                    spill_files_live: .metrics.spill_files_live,
                    tasks_live: .metrics.tasks_live}}'

step "2. hash join: orders.user_id = users.id (probe orders, build users)"
curl -s -X POST "${BASE}/query" -H 'content-type: application/json' -d '{
  "plan": {"op":"join","build_key":"id","probe_key":"user_id",
           "build":{"op":"scan","table":"users","batches":1,"batch_rows":256,"seed":7},
           "probe":{"op":"scan","table":"orders","batches":2,"batch_rows":64,"seed":7}},
  "timeout_ms": 10000
}' | jq '{run_id, status, row_count, columns, sample: .rows[0:3]}'

step "3. timeout: slow scan (100ms/batch) with a 250ms deadline -> HTTP 408 cancelled_timeout"
curl -s -o /tmp/pullq-timeout.json -w 'http_status=%{http_code}\n' \
  -X POST "${BASE}/query" -H 'content-type: application/json' -d '{
  "plan": {"op":"scan","table":"numbers","batches":50,"batch_rows":8,"delay_ms_per_batch":100},
  "timeout_ms": 250
}'
jq . /tmp/pullq-timeout.json

step "4. user cancel: start a slow query, cancel it by run id -> HTTP 499 cancelled_user"
curl -s -X POST "${BASE}/query" -H 'content-type: application/json' -d '{
  "plan": {"op":"scan","table":"numbers","batches":50,"batch_rows":8,"delay_ms_per_batch":100}
}' > /tmp/pullq-cancelled.json &
CURL_PID=$!
RUN_ID=""
for _ in $(seq 1 50); do
  RUN_ID=$(curl -s "${BASE}/metrics" | jq -r '.live[0].run_id // empty')
  [[ -n "${RUN_ID}" ]] && break
  sleep 0.1
done
echo "cancelling ${RUN_ID}"
curl -s -X POST "${BASE}/query/${RUN_ID}/cancel" | jq .
wait "${CURL_PID}" || true
jq . /tmp/pullq-cancelled.json

step "5. invalid plan: unknown table -> HTTP 400 input"
curl -s -o /tmp/pullq-invalid.json -w 'http_status=%{http_code}\n' \
  -X POST "${BASE}/query" -H 'content-type: application/json' -d '{
  "plan": {"op":"scan","table":"no_such_table"}
}'
jq . /tmp/pullq-invalid.json

step "6. leak check: metrics must show zero live resources after all queries"
curl -s "${BASE}/metrics" | jq '{queries_started, queries_finished, active_queries,
  live_memory: ([.live[].resources.memory_used_bytes] | add // 0),
  live_spill_files: ([.live[].resources.spill_files_live] | add // 0),
  live_tasks: ([.live[].resources.tasks_live] | add // 0)}'
echo "spill dir contents (must be empty):"
ls -A "${SPILL_DIR}" 2>/dev/null || echo "(empty)"

echo
echo "== demo complete; server log tail =="
tail -n 5 "${SERVER_LOG}"
