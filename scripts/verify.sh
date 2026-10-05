#!/usr/bin/env bash
# Full verification: build, run the whole CTest suite (unit/contract tests,
# independent benchmark, example), and list the structured run logs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$ROOT/tools/cmake/bin/cmake"

"$ROOT/scripts/build.sh"

echo "== ctest =="
"$CMAKE" --build "$ROOT/build" -j "$(nproc)" >/dev/null  # ensure up to date
(cd "$ROOT/build" && "$ROOT/tools/cmake/bin/ctest" --output-on-failure)

echo
echo "== run logs (JSONL, keyed by run_id) =="
ls -1 "$ROOT/build/logs" | tail -20
echo
echo "verify: OK"
