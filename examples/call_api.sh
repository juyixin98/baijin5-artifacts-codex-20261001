#!/usr/bin/env bash
# Service call examples for the Leapfrog Triejoin backend.
#
# Prerequisite: start the server first
#   cargo run --bin lfj-server            # default 127.0.0.1:8080
#   LFJ_BIND_PORT=18080 cargo run --bin lfj-server
#
# The JSON files under ../fixtures carry a "_comment" documentation field;
# the strict request schema rejects unknown fields, so the helper below strips
# it before POSTing. The same files are valid after that single transform.
set -euo pipefail

HOST="${LFJ_HOST:-127.0.0.1}"
PORT="${LFJ_PORT:-8080}"
BASE="http://${HOST}:${PORT}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fixdir="${here}/../fixtures"

post() {
  # $1 = fixture file; posts to JSON endpoint, pretty-prints a summary.
  local file="$1"
  python3 - "$file" <<'PY'
import json, sys, urllib.request
path = sys.argv[1]
doc = json.load(open(path))
doc.pop("_comment", None)
req = urllib.request.Request(
    "http://" + __import__("os").environ.get("LFJ_HOST", "127.0.0.1")
    + ":" + __import__("os").environ.get("LFJ_PORT", "8080") + "/api/v1/join",
    data=json.dumps(doc).encode(), headers={"content-type": "application/json"})
try:
    with urllib.request.urlopen(req) as r:
        d = json.load(r)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()); sys.exit(1)
print("decision     :", d["decision"])
print("row_count    :", d["row_count"])
print("columns      :", [c["name"] + ":" + c["type"] for c in d["columns"]])
print("first rows   :", d["rows"][:3])
print("counters     :", d["counters"])
print("truncated    :", d["truncated"], " next_cursor:", d.get("next_cursor"))
print("diagnostics  :", json.dumps(d["diagnostics"], indent=2)[:600])
PY
}

echo "### health"
curl -sS "${BASE}/health" | python3 -m json.tool

echo; echo "### triangle (expected 6 oriented tuples over K3)"
post "${fixdir}/triangle.json"

echo; echo "### skew (naive intermediate 1,000,000; output 1000)"
post "${fixdir}/skew.json"

echo; echo "### sparse (3 intersecting keys, ~4 seeks)"
post "${fixdir}/sparse.json"

echo; echo "### duplicates (multiplicity 3*2 = 6)"
post "${fixdir}/duplicates.json"

echo; echo "### null policy (drop_join_rows)"
post "${fixdir}/null_policy.json"

echo; echo "### expected rejection: disconnected graph (Cartesian product)"
curl -sS -X POST "${BASE}/api/v1/join" -H 'content-type: application/json' \
  -d '{"relations":[{"name":"a","schema":[{"name":"x","type":"int64"}],"rows":[[1]]},{"name":"b","schema":[{"name":"z","type":"int64"}],"rows":[[2]]}]}' \
  | python3 -m json.tool

echo; echo "### Arrow IPC endpoint (binary stream saved to /tmp/out.arrow)"
curl -sS -D - -o /tmp/out.arrow -X POST "${BASE}/api/v1/join/arrow" \
  -H 'content-type: application/json' \
  -d '{"fixtures":["sp_x","sp_y"],"limit":1000}' | grep -iE "content-type|x-lfj"
echo "bytes written: $(stat -c%s /tmp/out.arrow 2>/dev/null || echo 0)"
