#!/usr/bin/env bash
# Build all modules and produce the executable CLI jar. Runs tests; use
# ./scripts/test.sh alone to re-run tests only.
set -euo pipefail
cd "$(dirname "$0")/.."
mvn -q package
echo "built: mwis-cli/target/mwis-cli-1.0.0.jar"
