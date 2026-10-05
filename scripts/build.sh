#!/usr/bin/env bash
# Configures and builds the project with the project-local CMake.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$ROOT/tools/cmake/bin/cmake"

if [[ ! -x "$CMAKE" ]]; then
  echo "build: project-local cmake missing, running bootstrap first" >&2
  "$ROOT/scripts/bootstrap.sh"
fi

"$CMAKE" -S "$ROOT" -B "$ROOT/build" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$ROOT/build" -j "$(nproc)"
echo "build: done -> $ROOT/build"
