#!/usr/bin/env bash
# Native local build (no containers). Fetches pinned deps into ./deps on first
# run, then configures+builds with the project-local CMake.
set -euo pipefail
cd "$(dirname "$0")"

./tools/fetch_deps.sh
CMAKE="$PWD/deps/cmake-3.30.5-linux-x86_64/bin/cmake"

BUILD_TYPE="${BUILD_TYPE:-Release}"
"$CMAKE" -S . -B build -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
"$CMAKE" --build build -j"$(nproc)"
echo "BUILD_OK -> build/mp_eval build/polybench build/mp_unit_tests"
