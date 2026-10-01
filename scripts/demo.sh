#!/usr/bin/env bash
# Local native demo: builds with Maven (standard library + JUnit only) and runs
# every shipped synthetic fixture through the service entry.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG_DIR="${LOG_DIR:-logs/demo}"
mkdir -p "$LOG_DIR"

echo "== building (native JVM, no containers) =="
mvn -q -DskipTests package

JAR="target/rect-nonoverlap-kernel-1.0.0.jar"
run() {
  local f="$1"; shift
  echo
  echo "================ $f ================"
  set +e
  java -jar "$JAR" "examples/$f" --log-dir "$LOG_DIR" "$@"
  local code=$?
  set -e
  echo "(exit code: $code)"
}

run touch.txt --all 10
run unknown-safe.txt
run four-directions.txt
run required-conflict.txt
run all-diff.txt --all 10
run all-diff-unsat.txt
run zero-area.txt --all 100
run malformed.txt

echo
echo "== replay logs written under $LOG_DIR =="
ls -1 "$LOG_DIR" | tail -5
