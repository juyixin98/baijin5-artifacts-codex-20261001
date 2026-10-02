#!/usr/bin/env bash
# Local, self-contained demonstration. Uses only synthetic data and a loopback
# HTTP service; no external accounts or network calls.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$HERE/build"
CLI="$BUILD/src/service/pade_cli"
PORT="${PADE_DEMO_PORT:-18083}"

echo "== 1. version =="
"$CLI" version

echo "== 2. hand-computed exp [2/2] =="
"$CLI" gen-series --kind exp --terms 12 --out "$HERE/data/exp_12.txt"
"$CLI" approx --m 2 --n 2 --series "$HERE/data/exp_12.txt" --eval 0.25 \
  | sed -n '/^{"run_id".*matched_terms/p' || true
"$CLI" approx --m 2 --n 2 --series "$HERE/data/exp_12.txt" --eval 0.25 >/dev/null

echo "== 3. degeneracy: f=1+x^2 at [1/1] (q0=1 infeasible), expect exit 3 =="
set +e
"$CLI" approx --m 1 --n 1 --series "$HERE/data/poly_1px2.txt"
echo "    exit code = $? (3 = DegenerateDenominatorConstant)"
set -e

echo "== 4. rank-deficient with common factor: geometric series [2/2] =="
"$CLI" approx --m 2 --n 2 --kind geometric --terms 12 || true

echo "== 5. pole evaluation: exp [1/1] at x=2, expect exit 4 =="
set +e
"$CLI" approx --m 1 --n 1 --kind exp --terms 8 --eval 2 >/dev/null
echo "    exit code = $? (4 = DenominatorNearZero)"
set -e

echo "== 6. independent benchmark (reference: libm exp) =="
"$BUILD/pade_benchmark" --max-k 5 --points 7 --xmax 0.5

echo "== 7. loopback HTTP service =="
"$CLI" serve --port "$PORT" >/tmp/pade_serve.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for i in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
echo "-- health --"
curl -fsS "http://127.0.0.1:$PORT/health"; echo
echo "-- POST /approx exp [2/2] --"
curl -fsS -X POST "http://127.0.0.1:$PORT/approx" \
  -H 'Content-Type: application/json' \
  -d '{"m":2,"n":2,"coefficients":[1,1,0.5,0.166666666666666666,0.041666666666666666,0.008333333333333333]}'
echo
echo "-- POST /approx degenerate 1+x^2 [1/1] -> HTTP 422 --"
curl -s -o /tmp/pade_deg.json -w "http_status=%{http_code}\n" -X POST \
  "http://127.0.0.1:$PORT/approx" -H 'Content-Type: application/json' \
  -d '{"m":1,"n":1,"coefficients":[1,0,1,0,0,0,0]}'
cat /tmp/pade_deg.json; echo
kill $SRV 2>/dev/null || true
trap - EXIT
echo "== demo complete =="
