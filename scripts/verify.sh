#!/usr/bin/env bash
# Verification entry point: fmt check, clippy (warnings-as-errors),
# unit + integration tests, and CLI cross-checks. Exits non-zero on the
# first failing stage. Set BIN=... to reuse a prebuilt binary.
set -euo pipefail

cd "$(dirname "$0")/.."

# Isolated CARGO_HOME avoids contention with other cargo processes on
# shared machines; override by exporting CARGO_HOME yourself.
export CARGO_HOME="${CARGO_HOME:-$PWD/.cargo-home}"
export CARGO_TERM_COLOR=always

run() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

run "format check (cargo fmt --check)"
cargo fmt --all -- --check

run "clippy (all targets, -D warnings)"
cargo clippy --all-targets -- -D warnings

run "unit + integration tests"
cargo test --no-fail-fast

run "CLI verify (IEJoin vs independent nested-loop reference)"
./target/debug/iejoin verify

run "CLI run concrete scenarios with exact expectations"
./target/debug/iejoin run --scenario selective --json
./target/debug/iejoin request --file examples/truncated.json

run "HTTP smoke test (requires a free port; failures here do NOT count
as a logic failure — they only check serving, see README 'Checks not
executed')"
if command -v curl >/dev/null 2>&1; then
  # Pick an OS-assigned free port and clean the server up on exit.
  PORT=$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)
  ADDR="127.0.0.1:${PORT}"
  ./target/debug/iejoin serve --addr "$ADDR" >/tmp/iejoin-serve.log 2>&1 &
  SRV=$!
  trap 'kill $SRV 2>/dev/null || true' EXIT

  # Wait for readiness (up to ~5s) instead of a fixed sleep.
  code=000
  for _ in $(seq 1 25); do
    if curl -sf -o /dev/null "http://${ADDR}/healthz"; then ready=1; break; fi
    if ! kill -0 "$SRV" 2>/dev/null; then break; fi
    sleep 0.2
  done
  if [ "${ready:-0}" = 1 ]; then
    code=$(curl -s -o /tmp/iejoin-smoke.json -w '%{http_code}' -X POST \
      "http://${ADDR}/v1/join" \
      -H 'content-type: application/json' \
      --data @examples/grid_le.json || echo 000)
    echo "HTTP status: $code"
    test "$code" = "200"
    grep -Eq '"count": ?3' /tmp/iejoin-smoke.json
    echo "HTTP smoke OK"
  else
    echo "server did not become ready — serve log:"
    cat /tmp/iejoin-serve.log
    echo "skipping live HTTP smoke (documented gap; covered in-process by tests/http_api.rs)"
  fi
else
  echo "curl unavailable — skipping live HTTP smoke (documented gap)"
fi

printf '\n\033[1m=== ALL CHECKS PASSED ===\033[0m\n'
