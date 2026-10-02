#!/usr/bin/env bash
# Full local verification: build, vet, and all tests (unit + integration).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== go version =="
go version
echo "== go build ./... =="
go build ./...
echo "== go vet ./... =="
go vet ./...
echo "== go test ./... =="
go test ./... -count=1
