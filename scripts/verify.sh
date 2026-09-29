#!/usr/bin/env bash
# Reproducible verification entry point for the IEJoin range-join backend.
#
# Runs, in order: fmt check, clippy (warnings denied), unit + integration
# tests. Optional HTTP smoke test only if the server is already reachable;
# this script never assumes network access or external accounts.
#
# Override the toolchain commands with CARGO_BIN if needed.
set -euo pipefail

cd "$(dirname "$0")/.."

CARGO_BIN="${CARGO_BIN:-cargo}"

echo "==> cargo fmt --check"
"$CARGO_BIN" fmt --all -- --check

echo "==> cargo clippy (all targets, -D warnings)"
"$CARGO_BIN" clippy --all-targets -- -D warnings

echo "==> cargo test"
"$CARGO_BIN" test -- --nocapture

echo
echo "All executable checks passed."
echo
echo "NOT executed here (see README section 5):"
echo "  - cargo llvm-cov --fail-under-lines 80   (tool not installed)"
echo "  - cargo audit / cargo deny               (tools not installed)"
echo "Install them separately if those gates are required."
