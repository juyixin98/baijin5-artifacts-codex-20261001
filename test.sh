#!/usr/bin/env bash
# Full native verification: unit tests (assert concrete values + fail codes)
# and the shell-based CLI integration test.
set -euo pipefail
cd "$(dirname "$0")"
CT="$PWD/deps/cmake-3.30.5-linux-x86_64/bin"
[ -x build/mp_unit_tests ] || ./build.sh
"$CT/cmake" --build build -j"$(nproc)"
( cd build && "$CT/ctest" --output-on-failure )
