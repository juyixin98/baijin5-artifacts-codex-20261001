#!/usr/bin/env bash
# Start the line-delimited JSON TCP service and send one normal + one abnormal
# request. Native processes only; tears the server down on exit.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CARGO="${CARGO:-$HOME/.cargo/bin/cargo}"
ADDR="127.0.0.1:8147"

"$CARGO" build --quiet --bin fologic
"$CARGO" run --quiet --bin fologic -- serve --addr "$ADDR" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Wait for the listening socket (bounded).
for _ in $(seq 1 50); do
  if (exec 3<>"/dev/tcp/${ADDR%:*}/${ADDR##*:}") 2>/dev/null; then exec 3>&- 3<&-; break; fi
  sleep 0.1
done

send() {
  local label="$1" line="$2"
  echo "--- $label"
  echo "$line" | "$CARGO" run --quiet --example tcp_client -- --addr "$ADDR"
}

send "normal: alternating quantifiers" \
  "$(cat <<JSON
{"model_path":"fixtures/model/finite_model.json","formula_path":"fixtures/formulas/f03_alt_true.json","run_id":"srv-ok"}
JSON
)"

send "abnormal: unknown predicate" \
  '{"model_path":"fixtures/model/finite_model.json","run_id":"srv-bad","formula":{"op":"pred","name":"ghost","args":[]}}'

send "abnormal: malformed JSON" \
  '{not json'
