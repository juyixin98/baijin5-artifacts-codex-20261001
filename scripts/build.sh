#!/usr/bin/env bash
# Configure + build using the project-local CMake (native process, no Docker).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CMAKE="$ROOT/third_party/cmake/bin/cmake"
[ -x "$CMAKE" ] || { echo "run scripts/fetch_deps.sh first" >&2; exit 1; }
BUILD_TYPE="${1:-Release}"
"$CMAKE" -S "$ROOT" -B "$ROOT/build" -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
"$CMAKE" --build "$ROOT/build" -j"$(nproc)"
