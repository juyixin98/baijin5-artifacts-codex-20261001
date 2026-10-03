#!/usr/bin/env bash
# Native local build (no containers). Fetches deps on first run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -x tools/bin/cmake ] || bash scripts/setup_deps.sh
tools/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
tools/bin/cmake --build build -j "$(nproc 2>/dev/null || echo 2)"
