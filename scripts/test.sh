#!/usr/bin/env bash
# Run the full independent test suite (unit + integration + CLI smoke).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
if [[ ! -x .deps/cmake-3.30.5-linux-x86_64/bin/cmake ]]; then
  ./scripts/setup_deps.sh
fi
# shellcheck source=/dev/null
source .deps/env.sh
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
