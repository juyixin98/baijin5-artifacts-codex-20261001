#!/usr/bin/env bash
# Reproduce the full verification from a clean checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== go version =="
go version

echo "== generate synthetic fixtures (hand + go-asn1-ber) =="
go run ./cmd/genfixtures -out fixtures

echo "== gofmt check =="
unformatted="$(gofmt -l .)"
if [[ -n "${unformatted}" ]]; then
  echo "gofmt needs to format:" >&2
  echo "${unformatted}" >&2
  exit 1
fi

echo "== go vet =="
go vet ./...

echo "== unit + compatibility + integration tests with -race =="
go test -race ./...

echo "== coverage =="
go test -coverprofile=coverage.out ./...
go tool cover -func=coverage.out | tail -n 1

echo "ALL CHECKS PASSED"
