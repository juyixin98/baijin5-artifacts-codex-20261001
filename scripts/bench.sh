#!/usr/bin/env bash
# Run the standalone synthetic micro-benchmark.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
# shellcheck source=/dev/null
source .deps/env.sh
cmake --build build -j"$(nproc)"
./build/apps/bench_main "$@"
