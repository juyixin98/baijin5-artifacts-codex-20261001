#!/usr/bin/env bash
# Build (if needed) and execute the full test suite via CTest, then run the
# standalone test binary so per-case logs and the summary are visible.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${1:-$HERE/build}"
CMAKE="$HERE/third_party/cmake-3.30.5-linux-x86_64/bin/cmake"
[[ -x "$BUILD/tests/test_pade" ]] || "$HERE/scripts/build.sh" "$BUILD"
echo "[test] ctest"
( cd "$BUILD" && "$CMAKE" --build . --target test || true )
( cd "$BUILD" && ctest --output-on-failure )
echo "[test] standalone binary (verbose, with run ids)"
"$BUILD/tests/test_pade"
