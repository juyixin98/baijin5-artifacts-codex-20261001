#!/usr/bin/env bash
# Run the masked-division sample request through conversion + differential.
set -euo pipefail
cd "$(dirname "$0")/.."
go run ./cmd/simdc -request requests/example_masked_div.json
