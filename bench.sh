#!/usr/bin/env bash
# Run both local synthetic benchmarks and refresh bench/ artifacts.
set -euo pipefail
cd "$(dirname "$0")"
[ -x build/polybench ] || ./build.sh
mkdir -p bench
./build/polybench --domain FIELD --mod 1000000007 \
  --csv bench/bench_field.csv --md bench/complexity_field.md
./build/polybench --domain INTEGER \
  --csv bench/bench_integer.csv --md bench/complexity_integer.md
echo "BENCH_OK -> bench/bench_field.csv bench/bench_integer.csv"
