#!/usr/bin/env bash
# Full local validation; records raw outputs under docs/runs/.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/docs/runs"
mkdir -p "$OUT"

echo "== toolchain ==" | tee "$OUT/toolchain.txt"
(g++ --version | head -1; tools/bin/cmake --version | head -1) | tee -a "$OUT/toolchain.txt"

echo "== configure =="
tools/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release > "$OUT/configure.log" 2>&1
echo "configure rc=$?"

echo "== build =="
tools/bin/cmake --build build -j > "$OUT/build.log" 2>&1
echo "build rc=$?" | tee "$OUT/build.rc"

echo "== unit/contract tests =="
build/polyeval_tests > "$OUT/unit_tests.log" 2>&1
echo "unit rc=$? (see $OUT/unit_tests.log)"

echo "== ctest (incl CLI smoke) =="
(cd build && ../tools/bin/ctest --output-on-failure) > "$OUT/ctest.log" 2>&1
echo "ctest rc=$? (see $OUT/ctest.log)"

echo "== CLI demo (stdout+stderr) =="
build/polyeval eval --request examples/request_demo.txt \
  --config configs/default.conf --log "$OUT/demo.jsonl" --explain \
  > "$OUT/cli_stdout.jsonl" 2> "$OUT/cli_stderr.txt"
echo "cli rc=$? (1 expected: demo contains one rejected request)"

echo "== benchmark =="
build/polyeval_bench --mode exact --n 16,32,64,128,256 --degree 8 --repeat 3 \
  > "$OUT/bench_exact.jsonl" 2> "$OUT/bench_exact.err"
echo "bench exact rc=$?"
build/polyeval_bench --mode field --prime 1000003 --n 16,32,64,128,256,512 \
  --degree 16 --repeat 3 > "$OUT/bench_field.jsonl" 2> "$OUT/bench_field.err"
echo "bench field rc=$?"
echo "raw outputs under $OUT"
