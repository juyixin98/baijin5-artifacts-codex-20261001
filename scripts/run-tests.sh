#!/usr/bin/env bash
# Run the full test suite and keep the log for replay/audit.
# The log (including per-test run ids and intermediate states printed via
# tlog!) is kept under test-results/ with a timestamped copy per run.
set -uo pipefail
cd "$(dirname "$0")/.."

mkdir -p test-results
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="test-results/last-run.txt"

{
  echo "# test run $STAMP"
  echo "# rustc: $(rustc --version)"
  echo "# cmd: cargo test -- --nocapture"
  echo
  cargo test -- --nocapture
  code=$?
  echo
  echo "# exit_code=$code"
  exit $code
} 2>&1 | tee "$OUT"
code=${PIPESTATUS[0]}
cp "$OUT" "test-results/run-$STAMP.txt"
exit $code
