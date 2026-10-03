#!/usr/bin/env bash
# First-user smoke run: provision deps, configure, build, generate synthetic
# data, and execute a fit with the shipped profile. Native binaries only.
set -euo pipefail
cd "$(dirname "$0")/.."

CM=.toolchain/cmake-3.30.5-linux-x86_64/bin/cmake
if [[ ! -x "$CM" ]]; then
  bash scripts/setup_deps.sh
fi

"$CM" -S . -B build -DCMAKE_BUILD_TYPE=Release
"$CM" --build build -j4

for s in rigid2d similarity2d rigid3d collinear2d; do
  build/tools/gen_data/gen_data "$s" data
done

echo
echo "=== rigid2d (rotation-only) ==="
build/src/app/procrustes_fit --data data/rigid2d_pairs.csv \
  --config config/rigid2d.ini
