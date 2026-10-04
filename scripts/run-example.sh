#!/usr/bin/env bash
# Solve one example request. Usage: ./scripts/run-example.sh examples/request-path.json
set -euo pipefail
cd "$(dirname "$0")/.."
REQUEST="${1:-examples/request-path.json}"
JAR="mwis-cli/target/mwis-cli-1.0.0.jar"
if [ ! -f "$JAR" ]; then
  echo "jar missing, building first..." >&2
  mvn -q -DskipTests package
fi
if [ ! -d examples ] || [ -z "$(ls -A examples 2>/dev/null)" ]; then
  java -jar "$JAR" generate examples
fi
exec java -jar "$JAR" solve "$REQUEST"
