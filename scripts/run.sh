#!/usr/bin/env bash
# Build and run the service locally using configs/config.json.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p data
go build -o bin/berserv ./cmd/berserv
exec ./bin/berserv -config configs/config.json "$@"
