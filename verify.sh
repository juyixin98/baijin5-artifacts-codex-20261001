#!/usr/bin/env bash
# 一键验证：单元测试 + 打包 + CLI 冒烟（演示图对拍、预算耗尽与续扫一致性）
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/4 mvn test =="
mvn -q -B test
grep -h "Tests run" target/surefire-reports/*.txt

echo "== 2/4 mvn package =="
mvn -q -B package -DskipTests

JAR=target/bk-pivot-clique-1.0.0.jar

echo "== 3/4 demo + brute-check =="
java -jar "$JAR" --demo --brute-check

echo "== 4/4 budget exhaustion + resume consistency =="
set +e
OUT=$(java -jar "$JAR" --random --n 60 --p 0.6 --seed 11 --max-steps 2000 --print 0)
CODE=$?
set -e
echo "$OUT"
if [ "$CODE" -ne 3 ]; then
  echo "expected exit code 3 (RESOURCE_EXHAUSTED), got $CODE" >&2
  exit 1
fi
STATE=$(echo "$OUT" | sed -n 's/^resume state: //p')
PARTIAL=$(echo "$OUT" | sed -n 's/^partial cliques committed: //p')
RESUMED=$(java -jar "$JAR" --random --n 60 --p 0.6 --seed 11 --resume "$STATE" --print 0 | sed -n 's/^maximal cliques: //p')
FULL=$(java -jar "$JAR" --random --n 60 --p 0.6 --seed 11 --print 0 | sed -n 's/^maximal cliques: //p')
echo "partial=$PARTIAL resumed=$RESUMED full=$FULL"
if [ "$((PARTIAL + RESUMED))" -ne "$FULL" ]; then
  echo "resume consistency FAILED" >&2
  exit 1
fi
echo "resume consistency OK: $PARTIAL + $RESUMED = $FULL"
echo "ALL CHECKS PASSED"
