#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/build/logs/benchmark.$(date +%Y%m%d-%H%M%S).csv"
mkdir -p "$ROOT/build/logs"
"$ROOT/build/rlmf_bench" --repeats "${1:-3}" --csv "$OUT"
echo "benchmark csv: $OUT"
