#!/usr/bin/env bash
# Reproduce everything: unit/integration tests, then a live server run
# exercising the normal and abnormal endpoints, with all outputs saved
# under docs/examples-output/ for review.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=docs/examples-output
mkdir -p "$OUT"
PORT="${PORT:-18080}"

echo "== cargo test ==" | tee "$OUT/test-summary.txt"
cargo test --offline 2>&1 | grep -E '^(test |running|test result)' | tee -a "$OUT/test-summary.txt"

echo "== start server on :$PORT =="
PORT=$PORT RUST_LOG=iblt_service=info cargo run --offline --quiet \
    >"$OUT/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for i in $(seq 1 50); do
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
    sleep 0.2
done

req() { # req <name> <path> <json-body>
    local name=$1 path=$2 body=$3
    curl -s -w '\nHTTP %{http_code}\n' -X POST \
        -H 'content-type: application/json' \
        -d "$body" "http://127.0.0.1:$PORT$path" > "$OUT/$name.json"
    echo "--- $name"; cat "$OUT/$name.json"
}

curl -s "http://127.0.0.1:$PORT/health" | tee "$OUT/health.json"; echo

# Normal flow: A = 1..12, B = 7..18 -> only_a = 1..6, only_b = 13..18
req encode_a /v1/encode '{"keys":[1,2,3,4,5,6,7,8,9,10,11,12]}'
req encode_b /v1/encode '{"keys":[7,8,9,10,11,12,13,14,15,16,17,18]}'
TA=$(sed -n 's/.*"table_b64":"\([^"]*\)".*/\1/p' "$OUT/encode_a.json")
TB=$(sed -n 's/.*"table_b64":"\([^"]*\)".*/\1/p' "$OUT/encode_b.json")
req decode_ok /v1/decode "{\"table_a_b64\":\"$TA\",\"table_b_b64\":\"$TB\"}"

# Abnormal flows, one per error category.
req err_duplicate_keys /v1/encode '{"keys":[1,2,2,3]}'
req err_bad_json /v1/encode '{not json'
req err_bad_base64 /v1/decode '{"table_a_b64":"!!!","table_b_b64":"'"$TB"'"}'

# decode_incomplete: 40 differing keys squeezed into 16 cells.
OVERLOAD="[$(seq -s, 1 40)]"
req encode_overload /v1/encode "{\"keys\":$OVERLOAD,\"params\":{\"cells\":16,\"k\":3,\"seed\":5278865638494187777}}"
req encode_empty16 /v1/encode '{"keys":[],"params":{"cells":16,"k":3,"seed":5278865638494187777}}'
EMPTY16=$(sed -n 's/.*"table_b64":"\([^"]*\)".*/\1/p' "$OUT/encode_empty16.json")
TOVER=$(sed -n 's/.*"table_b64":"\([^"]*\)".*/\1/p' "$OUT/encode_overload.json")
req err_decode_incomplete /v1/decode "{\"table_a_b64\":\"$TOVER\",\"table_b_b64\":\"$EMPTY16\"}"

# state_conflict: tables with different cell counts.
req encode_32cells /v1/encode '{"keys":[1,2,3],"params":{"cells":32,"k":3,"seed":5278865638494187777}}'
T32=$(sed -n 's/.*"table_b64":"\([^"]*\)".*/\1/p' "$OUT/encode_32cells.json")
req err_state_conflict /v1/decode "{\"table_a_b64\":\"$TA\",\"table_b_b64\":\"$T32\"}"

kill $SERVER_PID 2>/dev/null || true
trap - EXIT
echo "== server log (run ids, intermediate state, decisions) =="
cat "$OUT/server.log"
echo "== done; outputs in $OUT =="
