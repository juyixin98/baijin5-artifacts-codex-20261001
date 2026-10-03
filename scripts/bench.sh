#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -x build/polyeval_bench ] || bash scripts/build.sh
echo "# exact mode"
build/polyeval_bench --mode exact --n 16,32,64,128,256 --degree 8 --repeat 3
echo "# field mode (p=1000003)"
build/polyeval_bench --mode field --prime 1000003 --n 16,32,64,128,256,512 --degree 16 --repeat 3
