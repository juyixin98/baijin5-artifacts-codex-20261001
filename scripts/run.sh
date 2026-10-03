#!/usr/bin/env bash
# Convenience wrapper for a single native run without running tests first.
# Usage: scripts/run.sh cycle:5 [budget] [--diag]
set -euo pipefail
cd "$(dirname "$0")/.."
JAR="target/vertex-coloring-1.0.0.jar"
if [[ ! -f "$JAR" ]]; then
  mvn -B -q package -DskipTests
fi
exec java -jar "$JAR" "$@"
