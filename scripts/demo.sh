#!/usr/bin/env bash
# Local demo: start blksched-server, compare deadline vs SCAN on the fixture
# traces, and print the explainable summaries. Requires: cargo, curl, python3.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-18080}"
BIND="127.0.0.1:${PORT}"
DATA_DIR="$(mktemp -d /tmp/blksched-demo-XXXXXX)"
export BLKSCHED_BIND="$BIND" BLKSCHED_DATA_DIR="$DATA_DIR"

echo "== building =="
cargo build --quiet

./target/debug/blksched-server &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true; rm -rf "$DATA_DIR"' EXIT

for i in $(seq 1 50); do
  curl -sf "http://${BIND}/v1/health" >/dev/null 2>&1 && break
  sleep 0.2
done

echo "== health / version =="
curl -s "http://${BIND}/v1/health"; echo
curl -s "http://${BIND}/v1/version"; echo

compare() {
  local trace="$1"
  echo
  echo "== compare deadline vs scan on trace '${trace}' =="
  curl -s -X POST "http://${BIND}/v1/runs/compare" \
    -H 'content-type: application/json' \
    -d "{\"trace\": {\"name\": \"${trace}\"}}" \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
for side in ("deadline", "scan"):
    s = r[side]
    rid = r[side + "_run_id"]
    print("  %-9s run=%s makespan=%sns seek_dist=%s completed=%s "
          "cancelled(before/after)=%s/%s deadline_misses=%s" % (
              side, rid, s["makespan_ns"], s["total_seek_distance_sectors"],
              s["completed"], s["cancelled_before_dispatch"],
              s["cancelled_after_dispatch"], s["deadline_misses"]))
c = r["comparison"]
print("  delta(scan-deadline): makespan=%sns seek_dist=%s misses=%s" % (
    c["makespan_delta_ns"], c["total_seek_distance_delta_sectors"],
    c["deadline_miss_delta"]))
for n in c["notes"]:
    print("  note:", n)
'
}

compare sequential
compare random
compare mixed_rw
compare boundary_cancel

echo
echo "== boundary_cancel event log (cancel semantics, request identities) =="
RUN_ID=$(curl -s -X POST "http://${BIND}/v1/runs" \
  -H 'content-type: application/json' \
  -d '{"trace": {"name": "boundary_cancel"}, "scheduler": {"kind": "deadline"}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
curl -s "http://${BIND}/v1/runs/${RUN_ID}/events" \
  | python3 -c '
import json, sys
for e in json.load(sys.stdin)["events"]:
    ids = ",".join(e["request_ids"])
    extra = e.get("detail") or ""
    reason = e.get("reason")
    if isinstance(reason, dict):
        reason = reason.get("type")
    print("  t=%5d  %-26s [%s] %s %s" % (e["t_ns"], e["kind"], ids, reason or "", extra))
'

echo
echo "== error semantics demo (unknown trace -> 404 NOT_FOUND) =="
curl -s -o /dev/null -w "  http_status=%{http_code} " \
  -X POST "http://${BIND}/v1/runs" -H 'content-type: application/json' \
  -d '{"trace": {"name": "no_such_trace"}, "scheduler": {"kind": "scan"}}'
curl -s -X POST "http://${BIND}/v1/runs" -H 'content-type: application/json' \
  -d '{"trace": {"name": "no_such_trace"}, "scheduler": {"kind": "scan"}}'
echo

echo
echo "== diagnostics =="
curl -s "http://${BIND}/v1/diagnostics/state" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  runs_stored:", d["runs_stored"]); print("  journal_dir:", d["journal_dir"]); [print("  caveat:", c) for c in d["device_caveats"]]'
echo
echo "demo done."
