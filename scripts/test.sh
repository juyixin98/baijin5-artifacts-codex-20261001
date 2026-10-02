#!/usr/bin/env bash
# Builds (if needed) and runs the full test binary; CTest is also wired up for
# `ctest` users. The replay log for this invocation lands in build/logs/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/build.sh" >/dev/null
mkdir -p "$ROOT/build/logs"
LOG="$ROOT/build/logs/rlmf-runs.$(date +%Y%m%d-%H%M%S).log"
"$ROOT/build/rlmf_tests" --log "$LOG"
echo "replay log: $LOG"
echo "index:      $LOG.index.csv"
