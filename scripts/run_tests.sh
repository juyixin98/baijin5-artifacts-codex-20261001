#!/usr/bin/env bash
# Full native verification: configure, build, CTest (unit + integration) and
# the shell-driven CLI integration test. No containers, no extra runtimes.
set -euo pipefail
cd "$(dirname "$0")/.."

CM=.toolchain/cmake-3.30.5-linux-x86_64/bin/cmake
[[ -x "$CM" ]] || bash scripts/setup_deps.sh

"$CM" -S . -B build -DCMAKE_BUILD_TYPE=Release
"$CM" --build build -j4

( cd build && ../.toolchain/cmake-3.30.5-linux-x86_64/bin/ctest --output-on-failure )
bash tests/integration/test_cli.sh

echo
echo "All unit, integration and CLI tests passed."
