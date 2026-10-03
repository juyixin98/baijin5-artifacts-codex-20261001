#!/usr/bin/env bash
# Service call examples against a locally running txmap server.
# Usage:  bash examples/curl_examples.sh [port]
# Captures responses into examples/outputs/ for review.
set -euo pipefail

PORT="${1:-8737}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/examples/outputs"
mkdir -p "$OUT"

export TXMAP_DB_PATH="${TXMAP_DB_PATH:-/tmp/txmap-examples.db}"
rm -f "$TXMAP_DB_PATH"

python3 -m uvicorn txmap.main:app --port "$PORT" --log-level warning &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  sleep 0.2
done

call() { # name, curl args...
  local name="$1"; shift
  echo "== $name =="
  curl -s "$@" | python3 -m json.tool | tee "$OUT/$name.json"
}

call 01_health "http://127.0.0.1:$PORT/health"
call 02_transcripts "http://127.0.0.1:$PORT/transcripts"
call 03_g2t_plus_ok -X POST "http://127.0.0.1:$PORT/map/genomic-to-transcript" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-plus-ok' \
  -d '{"transcript_id":"txA","position":44}'
call 04_g2t_minus_ok -X POST "http://127.0.0.1:$PORT/map/genomic-to-transcript" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-minus-ok' \
  -d '{"transcript_id":"txB","position":80}'
call 05_g2t_intronic_rejected -X POST "http://127.0.0.1:$PORT/map/genomic-to-transcript" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-intronic' \
  -d '{"transcript_id":"txA","position":20}'
call 06_g2t_out_of_contig -X POST "http://127.0.0.1:$PORT/map/genomic-to-transcript" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-ooc' \
  -d '{"transcript_id":"txA","position":120}'
call 07_g2t_unknown_transcript -X POST "http://127.0.0.1:$PORT/map/genomic-to-transcript" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-unknown-tx' \
  -d '{"transcript_id":"txZZZ","position":50}'
call 08_t_interval_minus_split -X POST "http://127.0.0.1:$PORT/map/transcript-interval" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-split' \
  -d '{"transcript_id":"txB","start":18,"end":22}'
call 09_g_interval_partial -X POST "http://127.0.0.1:$PORT/map/genomic-interval" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-partial' \
  -d '{"transcript_id":"txA","start":18,"end":32}'
call 10_roundtrip_ok -X POST "http://127.0.0.1:$PORT/validate/roundtrip" \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-roundtrip' \
  -d '{"transcript_id":"txA","space":"genomic","position":44}'
call 11_sequence_minus "http://127.0.0.1:$PORT/transcripts/txB/sequence"
call 12_provenance "http://127.0.0.1:$PORT/provenance/demo-intronic"

echo "outputs written to $OUT"
