#!/usr/bin/env bash
# Regenerate committed local synthetic fixtures (no external services).
set -euo pipefail
cd "$(dirname "$0")/.."
go run ./cmd/genfixtures -out testdata/generated
