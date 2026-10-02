#!/usr/bin/env bash
# Configure and build natively using the project-local CMake.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$HERE/third_party/cmake-3.30.5-linux-x86_64/bin/cmake"
BUILD="${1:-$HERE/build}"
if [[ ! -x "$CMAKE" ]]; then
  echo "[build] project CMake missing; running fetch_deps.sh" >&2
  "$HERE/scripts/fetch_deps.sh"
fi
"$CMAKE" -S "$HERE" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$BUILD" -j"$(nproc)"
echo "[build] artifacts in $BUILD"
