#!/usr/bin/env bash
# Build, vet and run the complete test suite (unit + black-box e2e) with the
# race detector, then report per-package coverage. All tests use in-process
# or loopback-only synthetic fixtures; no network beyond localhost.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export GOPROXY="${GOPROXY:-off}"
export GOSUMDB="${GOSUMDB:-off}"
export CGO_ENABLED=0

echo "==> go build"
go build ./...

echo "==> go vet"
go vet ./...

echo "==> go test (race + coverage)"
go test -race -count=1 -cover ./internal/... ./test/...

echo
echo "==> total coverage (internal packages)"
go test -count=1 -coverprofile=/tmp/imaplite-cover.out ./internal/... >/dev/null
go tool cover -func=/tmp/imaplite-cover.out | tail -1

echo
echo "All checks passed."
