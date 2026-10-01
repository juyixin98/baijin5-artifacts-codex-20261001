#!/usr/bin/env bash
# Full local verification: configure, build, run CTest and a CLI smoke demo.
# Native host only (Shell); no containers or extra language runtimes.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$HERE/cmake/cmake-3.30.5-linux-x86_64/bin/cmake"
CTEST="$HERE/cmake/cmake-3.30.5-linux-x86_64/bin/ctest"
BUILD="${BUILD_DIR:-$HERE/build-cmake}"

[[ -x "$CMAKE" ]] || { echo "run scripts/setup_deps.sh first"; exit 2; }

"$CMAKE" -S "$HERE" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$BUILD" -j"$(nproc)"

echo "=== CTest ==="
(cd "$BUILD" && "$CTEST" --output-on-failure)

echo "=== CLI smoke: prime length impulse ==="
"$BUILD/fft_cli" --length 977 --generator impulse --request-id verify-977

echo "=== independent benchmark ==="
"$BUILD/fft_bench" random 777 3

echo "VERIFY OK"
