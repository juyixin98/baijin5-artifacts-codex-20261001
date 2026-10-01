#!/usr/bin/env bash
# Full verification: compile + all JUnit tests (assert concrete results and
# failure categories, plus the independent-oracle cross check).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== mvn test =="
mvn test "$@"
echo
echo "== smoke: exit codes of the service entry =="
JAR="target/rect-nonoverlap-kernel-1.0.0.jar"
[ -f "$JAR" ] || mvn -q -DskipTests package
check() {
  local f="$1" want="$2"; shift 2
  set +e
  out=$(java -jar "$JAR" "examples/$f" --log-dir logs/verify "$@" 2>&1)
  code=$?
  set -e
  if [ "$code" -eq "$want" ]; then
    echo "OK   $f -> exit $code"
  else
    echo "FAIL $f -> exit $code (want $want)"
    echo "$out"
    exit 1
  fi
}
check touch.txt 0 --all 10
check unknown-safe.txt 0
check required-conflict.txt 10
check all-diff-unsat.txt 10
check malformed.txt 20
java -jar "$JAR" examples/four-directions.txt --max-nodes 1 --log-dir logs/verify >/dev/null 2>&1 \
  && rc=0 || rc=$?
if [ "$rc" -eq 40 ]; then echo "OK   four-directions budget -> exit 40"; else echo "FAIL budget exit $rc"; exit 1; fi
# forced overlap via preassignment -> state conflict 30
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
printf 'grid 2 2\nrect A 1 1 TRUE\nrect B 1 1 TRUE\nat A 1 1\nat B 1 1\n' > "$tmp/s.txt"
java -jar "$JAR" "$tmp/s.txt" --log-dir "$tmp/logs" >/dev/null 2>&1 && rc=0 || rc=$?
if [ "$rc" -eq 30 ]; then echo "OK   forced overlap -> exit 30"; else echo "FAIL state exit $rc"; exit 1; fi
echo
echo "ALL VERIFICATIONS PASSED"
