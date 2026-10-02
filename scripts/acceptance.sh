#!/usr/bin/env bash
# acceptance.sh - reproduce the full acceptance flow from a clean checkout.
# Every step logs its command and result; the script exits non-zero on the
# first failure and never reports an unknown state as success.
set -euo pipefail

cd "$(dirname "$0")/.."
LOG="${1:-ACCEPTANCE.log}"
: > "$LOG"

step() { echo "== [$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
run()  { echo "+ $*" | tee -a "$LOG"; "$@" 2>&1 | tee -a "$LOG"; }

step "environment"
run go version
run go env GOOS GOARCH
step "dependency versions (go.mod)"
run cat go.mod

step "build"
run go build ./...

step "unit + compatibility tests (verbose log in test-output.log)"
if go test -v ./... > test-output.log 2>&1; then
  echo "go test: PASS ($(grep -c 'verdict=PASS' test-output.log) logged verdicts)" | tee -a "$LOG"
else
  echo "go test: FAIL — see test-output.log" | tee -a "$LOG"
  exit 1
fi

step "start service on 127.0.0.1:18971 with temp config and database"
TMPD="$(mktemp -d)"
trap 'kill %1 2>/dev/null || true; rm -rf "$TMPD"' EXIT
cat > "$TMPD/berd.json" <<EOF
{
  "listen": "127.0.0.1:18971",
  "db_path": "$TMPD/audit.db",
  "limits": {
    "max_tag_bytes": 4, "max_length_bytes": 8, "max_depth": 32,
    "max_nodes": 10000, "max_input_bytes": 1048576,
    "max_integer_bytes": 1024, "max_bitstring_bytes": 1048576,
    "allow_indefinite": true
  }
}
EOF
go build -o "$TMPD/berd" ./cmd/berd
"$TMPD/berd" -config "$TMPD/berd.json" > "$TMPD/server.log" 2>&1 &
sleep 1

BASE=http://127.0.0.1:18971
req() { # req <name> <method> <path> [body]
  local name="$1" method="$2" path="$3" body="${4:-}"
  step "request: $name"
  if [ -n "$body" ]; then
    echo "+ curl -s -X $method $BASE$path -d '$body'" | tee -a "$LOG"
    curl -s -X "$method" "$BASE$path" -H 'Content-Type: application/json' -d "$body" | tee -a "$LOG"
  else
    echo "+ curl -s $BASE$path" | tee -a "$LOG"
    curl -s "$BASE$path" | tee -a "$LOG"
  fi
  echo | tee -a "$LOG"
}

req health GET /v1/health
req decode-integer POST /v1/decode '{"data":"0203010001"}'
req decode-nested-indefinite POST /v1/decode '{"data":"3080308002010500000000"}'
req decode-truncated POST /v1/decode '{"data":"0201"}'
req decode-stray-eoc POST /v1/decode '{"data":"0000"}'
req encode-sequence POST /v1/encode '{"form":"der","spec":{"type":"sequence","children":[{"type":"integer","value":"5"},{"type":"integer","value":"-129"}]}}'
req canonicalize POST /v1/canonicalize '{"data":"30800201050000"}'
req verify-der-ok POST /v1/verify-der '{"data":"02017f"}'
req verify-der-noncanonical POST /v1/verify-der '{"data":"0202007f"}'

step "server JSON log (run identity + per-request audit lines)"
run cat "$TMPD/server.log"

step "offline CLI cross-check"
run go run ./cmd/bercli decode 0203010001
run go run ./cmd/bercli der 30800201050000

step "ACCEPTANCE RESULT: all steps above completed without failure"
