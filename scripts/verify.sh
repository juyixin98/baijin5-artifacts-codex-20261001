#!/usr/bin/env bash
# Runs the full verification: vet + tests for every module, then builds
# the server and CLI binaries. Exits non-zero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."

# The machine-wide GOFLAGS=-mod=mod conflicts with workspace mode.
export GOFLAGS=-mod=readonly

MODULES="frontend ir runtime diff service tests"

for m in $MODULES; do
  echo "==> go vet ./$m/..."
  (cd "$m" && go vet ./...)
done

for m in $MODULES; do
  echo "==> go test ./$m/..."
  (cd "$m" && go test ./... )
done

echo "==> go build (server + cli)"
(cd service && go build ./...)

echo
echo "ALL CHECKS PASSED"
