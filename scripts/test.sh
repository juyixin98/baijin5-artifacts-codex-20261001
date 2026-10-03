#!/usr/bin/env bash
# Build (if needed) and run unit/contract tests + CLI smoke via CTest.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -x build/polyeval_tests ] || bash scripts/build.sh
cd build
../tools/bin/ctest --output-on-failure "$@"
