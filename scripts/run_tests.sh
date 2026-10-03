#!/usr/bin/env bash
# Build (if needed) and execute the full independent test suite via CTest.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CMAKE="$ROOT/third_party/cmake/bin/cmake"
"$HERE/build.sh" Release >/dev/null
cd "$ROOT/build"
"$CMAKE" --build . -j"$(nproc)"
ctest --output-on-failure -j"$(nproc)"
