#!/usr/bin/env bash
# Full verification: fmt, clippy, unit+integration tests (logs captured),
# release build, and a live server smoke test over HTTP.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/5 cargo fmt --check =="
cargo fmt --check

echo "== 2/5 cargo clippy (warnings are errors) =="
cargo clippy --all-targets -- -D warnings

echo "== 3/5 cargo test (structured logs -> target/test-logs.txt) =="
mkdir -p target
cargo test -- --nocapture 2>&1 | tee target/test-logs.txt | grep -E "^(test |test result)"

echo "== 4/5 cargo build --release =="
cargo build --release

echo "== 5/5 server smoke test (real HTTP against the release binary) =="
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT
MMAP_MODEL_BIND=127.0.0.1:19393 MMAP_MODEL_STORAGE_DIR="$SMOKE_DIR" \
  RUST_LOG=mmap_model=info ./target/release/mmap-model >"$SMOKE_DIR/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true; rm -rf "$SMOKE_DIR"' EXIT

for i in $(seq 1 50); do
  curl -sf http://127.0.0.1:19393/healthz >/dev/null && break
  sleep 0.1
done

check() { # check <name> <expected-substring> <actual>
  if [[ "$3" == *"$2"* ]]; then
    echo "  ok: $1"
  else
    echo "  FAIL: $1 — expected to contain '$2', got: $3" >&2
    exit 1
  fi
}

BASE=http://127.0.0.1:19393
check "healthz" "ok" "$(curl -sf $BASE/healthz)"
check "version" '"version":"0.1.0"' "$(curl -sf $BASE/version)"
check "create file" '"size":4096' \
  "$(curl -sf -XPOST $BASE/files -H 'content-type: application/json' -d '{"path":"smoke","size":4096}')"
MAP_ID=$(curl -sf -XPOST $BASE/mappings -H 'content-type: application/json' \
  -d '{"path":"smoke","offset":0,"length":4096,"kind":"shared"}' | grep -o '[0-9]*')
PAYLOAD=$(printf 'ab%.0s' $(seq 1 2048))  # 4096 bytes of 0xab as hex
curl -sf -XPOST "$BASE/mappings/$MAP_ID/write" -H 'content-type: application/json' \
  -d "{\"offset\":0,\"data_hex\":\"$PAYLOAD\"}" >/dev/null
check "dirty page visible" '"dirty_pages":[0]' "$(curl -sf "$BASE/files/state?path=smoke")"
check "sync persists" '"persisted":[0]' \
  "$(curl -sf -XPOST "$BASE/mappings/$MAP_ID/sync" -H 'content-type: application/json' -d '{}')"
check "persistent image" "abababab" \
  "$(curl -sf "$BASE/files/content?path=smoke&offset=0&length=4")"
check "truncate" '"zeroed_tail_page":0' \
  "$(curl -sf -XPOST $BASE/files/truncate -H 'content-type: application/json' -d '{"path":"smoke","size":2048}')"
check "unmap" '"kind":"shared"' "$(curl -sf -XDELETE "$BASE/mappings/$MAP_ID")"

echo "server log:"
sed 's/^/  /' "$SMOKE_DIR/server.log"
echo "ALL CHECKS PASSED"
