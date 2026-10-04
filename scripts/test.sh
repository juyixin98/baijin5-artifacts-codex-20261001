#!/usr/bin/env bash
# Run the full test suite across all modules.
set -euo pipefail
cd "$(dirname "$0")/.."
mvn test
