#!/usr/bin/env bash
# 本地演示：启动服务，用四个夹具轨迹跑 deadline vs SCAN 对比，打印关键指标。
set -euo pipefail

cd "$(dirname "$0")/.."
ADDR="127.0.0.1:18099"
BASE="http://$ADDR"

echo "== build =="
cargo build --quiet

echo "== start server =="
./target/debug/iosched-compare config/server.json &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for i in $(seq 1 50); do
  curl -sf "$BASE/v1/health" >/dev/null 2>&1 && break
  sleep 0.1
done
curl -s "$BASE/v1/health" | python3 -m json.tool

run_fixture() {
  local name="$1"
  echo
  echo "== fixture: $name =="
  local report
  report=$(curl -s -X POST "$BASE/v1/runs" -H 'content-type: application/json' \
    -d "{\"fixture_name\": \"$name\"}")
  echo "$report" | python3 -c '
import json, sys
report = json.load(sys.stdin)
print("run_id: %s  (service v%s)" % (report["run_id"], report["service_version"]))
for note in report.get("notes", []):
    print("  NOTE: %s" % note)
for r in report["results"]:
    m = r["metrics"]
    print("  [%-8s] submitted=%d completed=%d cancelled=%d misses=%d seek_sectors=%d max_wait=%.2fms makespan=%.2fms"
          % (r["scheduler"], m["submitted"], m["completed"], m["cancelled_queued"],
             m["deadline_misses"], m["total_seek_sectors"], m["max_wait_ms"], m["makespan_ms"]))
    print("            order: %s" % " -> ".join(m["dispatch_order"]))
'
  local run_id
  run_id=$(echo "$report" | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")
  echo "  -- dispatch reasons (from events log) --"
  curl -s "$BASE/v1/runs/$run_id/events" | python3 -c '
import json, sys
for line in sys.stdin:
    rec = json.loads(line)
    ev = rec["event"]
    if ev["kind"] == "dispatched":
        print("  t=%7.3f [%-8s] %s: %s" % (ev["at_ms"], rec["scheduler"], ",".join(ev["request_ids"]), ev["reason"]))
    elif ev["kind"] == "cancelled":
        print("  t=%7.3f [%-8s] cancel %s: %s (%s)" % (ev["at_ms"], rec["scheduler"], ev["request_id"], ev["outcome"], ev["detail"]))
'
}

for f in sequential random mixed_rw cancel_boundary; do
  run_fixture "$f"
done

echo
echo "== stored runs =="
curl -s "$BASE/v1/runs" | python3 -m json.tool
echo
echo "demo done. reports persisted under ./data/runs/"
