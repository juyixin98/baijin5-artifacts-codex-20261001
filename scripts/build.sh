#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CMAKE="$ROOT/deps/cmake-3.28.3-linux-x86_64/bin/cmake"
[ -x "$CMAKE" ] || { echo "run scripts/fetch_deps.sh first" >&2; exit 1; }
"$CMAKE" -S "$ROOT" -B "$ROOT/build" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$ROOT/build" -j
