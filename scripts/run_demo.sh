#!/usr/bin/env bash
# End-to-end demo: builds the server, starts it, exercises the API (normal
# and abnormal cases), and stores every artifact under docs/evidence/.
set -euo pipefail
cd "$(dirname "$0")/.."

EVIDENCE=docs/evidence
PORT=8487
BASE="http://127.0.0.1:$PORT"
mkdir -p "$EVIDENCE" data

echo "== cargo test (full output -> $EVIDENCE/test-run.txt) =="
cargo test 2>&1 | tee "$EVIDENCE/test-run.txt"

echo "== cargo build =="
cargo build 2>&1 | tail -1

rm -rf data/outputs data/runs.jsonl
RUST_LOG=info ./target/debug/merge-checker-server > "$EVIDENCE/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
sleep 1

S="$EVIDENCE/server-session.txt"
: > "$S"
{
echo '### 1. GET /v1/health'
curl -sS "$BASE/v1/health" | jq .
echo
echo '### 2. POST /v1/merges — good stack (x-request-id: req-demo-001)'
curl -sS -D /dev/stdout -o /tmp/demo-merge-good.json -X POST "$BASE/v1/merges" \
  -H 'content-type: application/json' -H 'x-request-id: req-demo-001' \
  -d '{"layers":["fixtures/layers/layer1","fixtures/layers/layer2","fixtures/layers/layer3"],"output_dir":"data/outputs/demo-good"}' \
  | grep -i '^x-request-id'
jq '{run_id, request_id, tool_version, status, entries: [.entries[] | {path, kind, layer: .source.index}], failures, uncertainties}' /tmp/demo-merge-good.json
RUN_ID=$(jq -r .run_id /tmp/demo-merge-good.json)
echo
echo '### 3. produced tree vs hand-authored reference: diff -r fixtures/expected/final-tree data/outputs/demo-good'
diff -r fixtures/expected/final-tree data/outputs/demo-good && echo 'IDENTICAL'
echo
echo '### 4. POST /v1/merges — malicious stack (x-request-id: req-demo-002)'
curl -sS -X POST "$BASE/v1/merges" \
  -H 'content-type: application/json' -H 'x-request-id: req-demo-002' \
  -d '{"layers":["fixtures/malicious/layer-hardlink","fixtures/malicious/layer-escape","fixtures/malicious/layer-abslink","fixtures/malicious/layer-badwhiteout"],"output_dir":"data/outputs/demo-malicious"}' \
  | jq '{run_id, request_id, status, failures, uncertainties}'
echo
echo '### 5. external directory untouched (content + listing)'
cat fixtures/external/sentinel.txt
ls fixtures/external
echo
echo "### 6. GET /v1/merges/$RUN_ID/report — human-readable report"
curl -sS "$BASE/v1/merges/$RUN_ID/report" -H 'x-request-id: req-demo-003'
echo
echo '### 7. POST with layer outside workspace -> 400 typed error'
curl -sS -w '\nHTTP %{http_code}\n' -X POST "$BASE/v1/merges" \
  -H 'content-type: application/json' \
  -d '{"layers":["/etc"],"output_dir":"data/outputs/never"}'
echo
echo '### 8. GET unknown run -> 404 typed error'
curl -sS -w '\nHTTP %{http_code}\n' "$BASE/v1/merges/run-nope"
} 2>&1 | tee -a "$S"

echo "== persisted run store (data/runs.jsonl, $(wc -l < data/runs.jsonl) runs) =="
cp data/runs.jsonl "$EVIDENCE/runs.jsonl"
kill $SERVER_PID 2>/dev/null || true
trap - EXIT
echo "evidence written to $EVIDENCE/"
