#!/usr/bin/env bash
# Native Linux build (no containers). Bootstraps project-local CMake + Eigen
# on first use, configures, builds, regenerates fixtures and runs ctest.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -x .deps/cmake-3.30.5-linux-x86_64/bin/cmake ]]; then
  ./scripts/setup_deps.sh
fi
# shellcheck source=/dev/null
source .deps/env.sh

BUILD_TYPE="${BUILD_TYPE:-Release}"
echo "[build] configuring (${BUILD_TYPE})"
cmake -S . -B build -DCMAKE_BUILD_TYPE="${BUILD_TYPE}"
echo "[build] compiling"
cmake --build build -j"$(nproc)"
echo "[build] regenerating synthetic fixtures"
./build/apps/datagen --outdir data
echo "[build] running ctest"
ctest --test-dir build --output-on-failure
