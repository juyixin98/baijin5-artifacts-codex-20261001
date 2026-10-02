#!/usr/bin/env bash
# Reproducible real-run demonstration for the stunlab STUN Binding lab.
#
# It exercises, over real loopback UDP, both the normal and abnormal paths and
# records every result under evidence/runs/<run-id>/ :
#   - console transcript (transcript.txt)
#   - server/client JSONL event logs
#   - SQLite evidence databases
#   - independent Python-oracle verdicts
#
# Usage:  ./scripts/run_demo.sh
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_ID="demo-$(date -u +%Y%m%dT%H%M%SZ)"
OUT="evidence/runs/$RUN_ID"
mkdir -p "$OUT"
TR="$OUT/transcript.txt"
KEY="lab-shared-secret"
PORT=34478
PORT6=34479
SILENT=34490

say() { echo; echo "==== $* ===="; }
{
say "run id: $RUN_ID"
echo "go: $(go version)"
echo "python: $(python3 --version 2>&1)"

# ---------------------------------------------------------------------------
say "1. build binaries (offline, vendored deps)"
CGO_ENABLED=1 go build -mod=vendor -o bin/stund ./cmd/stund
CGO_ENABLED=1 go build -mod=vendor -o bin/stunc ./cmd/stunc
echo "built: $(ls bin)"

# ---------------------------------------------------------------------------
say "2. start integrity-protected stund on IPv4 and IPv6"
./bin/stund -net udp4 -addr "127.0.0.1:$PORT"  -key "$KEY" \
  -db "$OUT/stund4.db" -log "$OUT/stund4.jsonl" -note "demo ipv4" \
  >"$OUT/stund4.stdout" 2>&1 &
SRV4=$!
./bin/stund -net udp6 -addr "[::1]:$PORT6" -key "$KEY" \
  -db "$OUT/stund6.db" -log "$OUT/stund6.jsonl" -note "demo ipv6" \
  >"$OUT/stund6.stdout" 2>&1 &
SRV6=$!
# A silent UDP socket (via python) to drive real client timeouts.
python3 - "$SILENT" <<'PY' >"$OUT/silent.stdout" 2>&1 &
import socket, sys, time
p = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", p))
time.sleep(45)
PY
SILENT_PID=$!
sleep 0.8

cleanup() {
  kill "$SRV4" "$SRV6" "$SILENT_PID" "${SRVN:-0}" 2>/dev/null || true
  wait "$SRV4" "$SRV6" "$SILENT_PID" "${SRVN:-0}" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
say "3. NORMAL: Go client -> Go server over IPv4 (expect exit 0, endpoint echoed)"
./bin/stunc -server "127.0.0.1:$PORT" -key "$KEY" -timeout 1s \
  -db "$OUT/stunc.db" -log "$OUT/stunc.jsonl" -note "demo normal ipv4"
echo "exit=$?"

say "4. NORMAL: Go client -> Go server over IPv6"
./bin/stunc -net udp6 -server "[::1]:$PORT6" -key "$KEY" -timeout 1s \
  -db "$OUT/stunc6.db" -log "$OUT/stunc6.jsonl" -note "demo normal ipv6"
echo "exit=$?"

say "5. ABNORMAL: Go client with WRONG KEY (server silently discards -> timeout, exit 3)"
./bin/stunc -server "127.0.0.1:$PORT" -key "wrong-key" -timeout 600ms \
  -db "$OUT/stunc-wrong.db" -log "$OUT/stunc-wrong.jsonl" -note "wrong key"
echo "exit=$? (expected 3)"

say "6. ABNORMAL: Go client to a SILENT port (retransmit then timeout, exit 3)"
./bin/stunc -server "127.0.0.1:$SILENT" -timeout 600ms -retries 2 \
  -db "$OUT/stunc-timeout.db" -log "$OUT/stunc-timeout.jsonl" -note "timeout"
echo "exit=$? (expected 3)"

# ---------------------------------------------------------------------------
say "7. INTEROP: independent Python client -> Go server (IPv4 + IPv6)"
python3 test/oracle/stun_oracle.py bind "127.0.0.1:$PORT" --key "$KEY"
echo "exit=$? (expected 0)"
python3 test/oracle/stun_oracle.py bind "[::1]:$PORT6" --net udp6 --key "$KEY"
echo "exit=$? (expected 0)"

say "8. INTEROP: Python client sends UNKNOWN REQUIRED attr -> Go 420"
python3 test/oracle/stun_oracle.py bind "127.0.0.1:$PORT" --key "$KEY" --expect-error 420
echo "exit=$? (expected 0)"

# ---------------------------------------------------------------------------
say "9. start an UNSIGNED stund; Python client expecting integrity must reject it"
./bin/stund -net udp4 -addr "127.0.0.1:34480" \
  -db "$OUT/stund-nokey.db" -log "$OUT/stund-nokey.jsonl" -note "no key" \
  >"$OUT/stund-nokey.stdout" 2>&1 &
SRVN=$!
sleep 0.6
python3 test/oracle/stun_oracle.py bind "127.0.0.1:34480" --key "expects-mi"
echo "exit=$? (expected 2, integrity_failure)"

# ---------------------------------------------------------------------------
sleep 0.3

say "10. SQLite evidence: exchanges recorded by the IPv4 stund"
python3 - "$OUT/stund4.db" <<'PY'
import sqlite3, sys, json
con = sqlite3.connect(sys.argv[1])
con.row_factory = sqlite3.Row
rows = con.execute("SELECT txn_id, remote_addr, family, outcome, mapped_ip, mapped_port,"
                   " error_code, substr(detail,1,60) AS detail FROM exchanges ORDER BY id").fetchall()
for r in rows:
    print(dict(r))
print(f"total exchanges: {len(rows)}")
# Distinct failure categories actually observed.
cats = {r["outcome"] for r in rows}
print("observed categories:", sorted(cats))
assert "success" in cats, "expected at least one success"
assert "integrity_failure" in cats, "wrong-key request must be recorded as integrity_failure"
print("ASSERTIONS PASSED")
PY

say "11. JSONL evidence: distinct error_kind values seen by clients"
cat "$OUT"/stunc*.jsonl 2>/dev/null | python3 -c '
import sys, json
kinds = {}
for line in sys.stdin:
    ev = json.loads(line)
    f = ev.get("fields", {})
    if "error_kind" in f:
        kinds[f["error_kind"]] = kinds.get(f["error_kind"], 0) + 1
print(kinds)
'

echo
echo "artifacts written to $OUT"
} 2>&1 | tee "$TR"

echo
echo "Demo complete. Transcript: $TR"
