#!/usr/bin/env bash
# Local demo: build the jar and solve every example, printing exit codes.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== build =="
mvn -q -DskipTests package
JAR=$(ls target/rect-nonoverlap-kernel-*.jar | head -n1)

run() {
  local file="$1"; shift
  echo "== solve ${file} $* =="
  set +e
  java -jar "${JAR}" solve "${file}" --log-dir target/run-logs "$@"
  local code=$?
  set -e
  echo "exit=${code}"
  echo
}

run examples/sat-basic.txt --all
run examples/unsat-overlap.txt
run examples/optional-absorb.txt
run examples/zero-area.txt --all
run examples/backtrack-presence.txt --all
run examples/limit-demo.txt

echo "run logs: target/run-logs/"
