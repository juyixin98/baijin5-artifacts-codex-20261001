#!/usr/bin/env bash
# Runnable end-to-end examples against a locally running TensorCraft server.
# Start it first with ./scripts/run_server.sh
set -euo pipefail

BASE="${TENSORCRAFT_BASE:-http://127.0.0.1:8000/api/v1}"
pp() { python3 -m json.tool; }
post() { curl -s -X POST "$BASE/$1" -H 'content-type: application/json' -d "$2"; }

echo "== create a (2,3) int64 tensor 'a' =="
post tensors '{"data":[[1,2,3],[4,5,6]],"dtype":"int64","handle":"a"}' | pp

echo "== transpose (zero-copy view) =="
curl -s -X POST "$BASE/tensors/a/transpose" -H 'content-type: application/json' \
  -d '{}' -o /tmp/at.json
AT=$(python3 -c 'import json;print(json.load(open("/tmp/at.json"))["handle"])')
echo "transposed handle: $AT"
cat /tmp/at.json | pp

echo "== reshape the TRANSPOSED tensor to (6,) -> must copy =="
curl -s -X POST "$BASE/tensors/$AT/reshape" -H 'content-type: application/json' \
  -d '{"shape":[6],"order":"C"}' | pp

echo "== reshape the ORIGINAL a to (6,) -> zero-copy view =="
post tensors/a/reshape '{"shape":[6],"order":"C"}' | \
  python3 -c 'import json,sys; b=json.load(sys.stdin); print("copied:", b["copied"], "shape:", b["tensor"]["shape"])'

echo "== negative-step slice (reverse) =="
post tensors/a/slice '{"index":[[null,null,-1]]}' | \
  python3 -c 'import json,sys; b=json.load(sys.stdin); print("strides:", b["tensor"]["strides"], "values via /values")'

echo "== build a self-overlapping (3,3) sliding window over 5 elements =="
post tensors '{"data":[1,2,3,4,5],"dtype":"int64","handle":"base"}' >/dev/null
curl -s -X POST "$BASE/tensors/base/strided-view" \
  -H 'content-type: application/json' \
  -d '{"shape":[3,3],"strides":[1,1],"offset":0}' -o /tmp/win.json
WIN=$(python3 -c 'import json;print(json.load(open("/tmp/win.json"))["handle"])')
python3 -c 'import json;b=json.load(open("/tmp/win.json"));print("overlapping:",b["self_overlapping"])'

echo "== write into the overlapping view with policy=reject -> HTTP 409 =="
curl -s -o /tmp/ov.json -w "status=%{http_code}\n" -X POST \
  "$BASE/tensors/$WIN/assign/scalar" -H 'content-type: application/json' \
  -d '{"value":7,"policy":"reject"}'
python3 -c 'import json;b=json.load(open("/tmp/ov.json"));print("category:",b["error"]["category"]);print("details:",b["error"]["details"])'

echo "== same write with policy=temp_copy -> deterministic success =="
curl -s -X POST "$BASE/tensors/$WIN/assign/scalar" \
  -H 'content-type: application/json' -d '{"value":7,"policy":"temp_copy"}' | \
  python3 -c 'import json,sys;b=json.load(sys.stdin);print("report:",b["report"])'

echo "== differential verification: core vs independent NumPy oracle =="
curl -s "$BASE/verify/all" | \
  python3 -c 'import json,sys; b=json.load(sys.stdin); print("ok:", b["ok"], b["totals"])'

echo
echo "All examples completed."
