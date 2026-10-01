#!/usr/bin/env bash
# End-to-end controlled-UDP reproduction for the local STUN Binding stack.
#
# Runs entirely on loopback with synthetic data. Produces under results/:
#   audit.db                 SQLite audit trail written by the live server
#   e2e_server.log           server stderr (bound address, run id)
#   e2e_client_normal.log    successful IPv4 Binding calls (stdout)
#   e2e_client_events.jsonl  client state-machine events (JSONL)
#   e2e_anomalies.log        one row per injected anomaly with judgment
#   e2e_summary.txt          human-readable summary
#
# Exit status is non-zero if any expected judgment mismatches.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEY="localstun-test-key"
RUNID="run-$(date -u +%Y%m%dT%H%M%SZ)"
RES="results"
mkdir -p "$RES"
DB="$RES/audit.db"
rm -f "$DB"

# Pure-Go SQLite must build without cgo; pin the module proxy off-line if a
# vendor/cache is intended. Default uses the normal module cache + go.sum.
export GOFLAGS="-mod=mod"
export GOPROXY="${GOPROXY:-off}"

echo "== build =="
go build -o "$RES/stund"  ./cmd/stund
go build -o "$RES/stunc"  ./cmd/stunc
go build -o "$RES/stuninject" ./cmd/stuninject

# Pick an ephemeral port by asking the OS via the server itself (addr :0);
# read back the bound port from its log.
echo "== start server (run=$RUNID) =="
"$RES/stund" -addr "127.0.0.1:0" -key "$KEY" -fingerprint \
  -db "$DB" -run-id "$RUNID" 2>"$RES/e2e_server.log" &
SRV_PID=$!
trap 'kill "$SRV_PID" >/dev/null 2>&1 || true' EXIT

# Wait for the bound address line.
for _ in $(seq 1 100); do
  if grep -q "listening on" "$RES/e2e_server.log"; then break; fi
  sleep 0.02
done
SRV_ADDR="$(sed -n 's/.*listening on \([^ ]*\).*/\1/p' "$RES/e2e_server.log" | head -1)"
if [[ -z "$SRV_ADDR" ]]; then
  echo "server failed to bind; see $RES/e2e_server.log" >&2
  exit 1
fi
echo "server at $SRV_ADDR"

echo "== normal client traffic (IPv4, integrity + fingerprint) =="
set +e
"$RES/stunc" -server "$SRV_ADDR" -key "$KEY" -fingerprint -count 3 \
  -run-id "${RUNID}-client" 2>"$RES/e2e_client_events.jsonl" \
  | tee "$RES/e2e_client_normal.log"
NORMAL_RC=${PIPESTATUS[0]}
set -e

echo "== anomaly injection =="
: > "$RES/e2e_anomalies.log"
inject() {
  local mode="$1" expect="$2"
  echo "--- mode=$mode expect=$expect" | tee -a "$RES/e2e_anomalies.log"
  "$RES/stuninject" -server "$SRV_ADDR" -key "$KEY" \
    -mode "$mode" -expect "$expect" 2>&1 | tee -a "$RES/e2e_anomalies.log"
}
RC=0
inject valid            success    || RC=1
inject unsigned         error401   || RC=1
inject unknown-required error420   || RC=1
inject unknown-optional success    || RC=1
inject junk             dropped    || RC=1
inject bad-cookie       dropped    || RC=1
inject tampered         dropped    || RC=1

echo "== IPv6 (skipped automatically if ::1 is unavailable) =="
"$RES/stund" -addr "[::1]:0" -key "$KEY" -fingerprint -db "" 2>"$RES/e2e_server_v6.log" &
SRV6_PID=$!
sleep 0.2
SRV6_ADDR="$(sed -n 's/.*listening on \([^ ]*\).*/\1/p' "$RES/e2e_server_v6.log" | head -1)"
if [[ -n "$SRV6_ADDR" ]]; then
  "$RES/stunc" -server "$SRV6_ADDR" -local "[::1]:0" -key "$KEY" \
    -fingerprint -count 1 2>>"$RES/e2e_client_events.jsonl" \
    | tee "$RES/e2e_client_v6.log" || echo "IPv6 client failed (may be environmental)"
else
  echo "IPv6 loopback unavailable; skipping" | tee "$RES/e2e_client_v6.log"
fi
kill "$SRV6_PID" >/dev/null 2>&1 || true

echo "== audit trail (SQLite) =="
python3 - "$DB" "$RUNID" > "$RES/e2e_summary.txt" <<'PY'
import sqlite3, sys
db, run = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
cur = con.cursor()
print(f"run: {run}")
print("events by (event, kind):")
for event, kind, n in cur.execute(
    "SELECT event, kind, COUNT(*) FROM audit_events WHERE run_id=? GROUP BY event, kind ORDER BY 3 DESC",
    (run,)):
    print(f"  {n:3d}  {event:30s} kind={kind or '-'}")
print("\nfirst 8 replayable rows (seq, kind, txid, src, detail):")
for seq, kind, tx, src, detail in cur.execute(
    "SELECT seq, kind, tx_id_hex, src_addr, substr(detail,1,60) "
    "FROM audit_events WHERE run_id=? ORDER BY seq LIMIT 8", (run,)):
    print(f"  #{seq:02d} [{kind or 'ok':9s}] tx={tx[:12]}.. src={src:22s} {detail}")
total = cur.execute("SELECT COUNT(*) FROM audit_events WHERE run_id=?", (run,)).fetchone()[0]
print(f"\ntotal audited datagrams for run: {total}")
PY
cat "$RES/e2e_summary.txt"

echo
if [[ $NORMAL_RC -eq 0 && $RC -eq 0 ]]; then
  echo "REPRODUCTION RESULT: ALL JUDGMENTS MATCH"
else
  echo "REPRODUCTION RESULT: FAILURE (normal_rc=$NORMAL_RC anomaly_rc=$RC)"
  exit 1
fi
