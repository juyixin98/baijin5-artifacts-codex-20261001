#!/usr/bin/env bash
# Build (if needed) and run the independent test suite.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${1:-$ROOT/build}"
[[ -x "$BUILD/pade_tests" ]] || bash "$ROOT/scripts/build.sh" "$BUILD"
"$ROOT/third_party/cmake-bin/ctest" --test-dir "$BUILD" --output-on-failure
