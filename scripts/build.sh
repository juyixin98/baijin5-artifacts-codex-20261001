#!/usr/bin/env bash
# Native local build (no containers). Fetches deps if needed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$ROOT/third_party/cmake-bin/cmake"
if [[ ! -x "$CMAKE" ]]; then bash "$ROOT/scripts/setup_deps.sh"; fi
BUILD="${1:-$ROOT/build}"
"$CMAKE" -S "$ROOT" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$BUILD" -j"$(nproc)"
echo "[build] artifacts in $BUILD"
