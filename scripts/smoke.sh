#!/usr/bin/env bash
# Live smoke test against a real uvicorn process (normal + abnormal requests).
# Records every request/response under docs/run-output.txt for review.
set -euo pipefail
cd "$(dirname "$0")/.."

export TXMAP_DB="$(pwd)/data/txmap.live.sqlite3"
BASE="http://127.0.0.1:8137"

rm -f "$TXMAP_DB"
PYTHONPATH=src python3 -m uvicorn txmap.api.app:app --host 127.0.0.1 --port 8137 \
    > /tmp/txmap-uvicorn.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

echo "== waiting for server =="
for _ in $(seq 1 40); do
  if curl -sf "$BASE/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done

call() {
  local title="$1"; shift
  echo; echo "### $title"
  echo "\$ $*"
  "$@"
}

CURL=(curl -sS -w '\nHTTP %{http_code}\n')

call "health" "${CURL[@]}" "$BASE/health"
call "list transcripts" "${CURL[@]}" "$BASE/transcripts"

call "PLUS point tx=30 -> genomic (expect 160)" \
  "${CURL[@]}" -X POST "$BASE/map/tx-to-genomic/point" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T1_PLUS","position":30,"request_id":"smoke-1"}'

call "PLUS interval tx[25,55) -> 3 fragments, 30 nt" \
  "${CURL[@]}" -X POST "$BASE/map/tx-to-genomic/interval" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T1_PLUS","start":25,"end":55,"request_id":"smoke-2"}'

call "MINUS point genomic=639 -> tx=0" \
  "${CURL[@]}" -X POST "$BASE/map/genomic-to-tx/point" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T2_MINUS","position":639,"request_id":"smoke-3"}'

call "MINUS interval tx[15,50) -> descending-genomic fragments" \
  "${CURL[@]}" -X POST "$BASE/map/tx-to-genomic/interval" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T2_MINUS","start":15,"end":50,"request_id":"smoke-4"}'

call "ABNORMAL intronic point g=145 (expect 422 intronic_position)" \
  "${CURL[@]}" -X POST "$BASE/map/genomic-to-tx/point" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T1_PLUS","position":145,"request_id":"smoke-5"}'

call "ABNORMAL region over intron [125,165) (expect 422 region_not_mappable)" \
  "${CURL[@]}" -X POST "$BASE/map/genomic-to-tx/interval" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T1_PLUS","start":125,"end":165,"request_id":"smoke-6"}'

call "ABNORMAL out of range tx=80 (expect 422 coordinate_out_of_range)" \
  "${CURL[@]}" -X POST "$BASE/map/tx-to-genomic/point" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"T1_PLUS","position":80,"request_id":"smoke-7"}'

call "ABNORMAL unknown transcript (expect 404)" \
  "${CURL[@]}" -X POST "$BASE/map/tx-to-genomic/point" \
  -H 'content-type: application/json' \
  -d '{"transcript_id":"GHOST","position":0,"request_id":"smoke-8"}'

call "audit trail for smoke-2 (accepted interval)" \
  "${CURL[@]}" "$BASE/audit/smoke-2"
call "audit trail for smoke-5 (rejected intronic)" \
  "${CURL[@]}" "$BASE/audit/smoke-5"

echo; echo "== server log =="
cat /tmp/txmap-uvicorn.log
