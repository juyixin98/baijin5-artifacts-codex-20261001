#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$ROOT/.local/cmake/bin/cmake"
[[ -x "$CMAKE" ]] || { echo "run ./scripts/setup.sh first"; exit 1; }
"$CMAKE" -S "$ROOT" -B "$ROOT/build" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$ROOT/build" -j"$(nproc)"
