#!/usr/bin/env bash
# Full verification: clean build + all independent tests. Runs on the native
# Linux host with the pre-installed JDK 21 and Maven; no containers.
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/3] clean compile"
mvn -B -q clean compile

echo "[2/3] run all JUnit 5 tests"
mvn -B test

echo "[3/3] run the demo (optimal + budget-stopped)"
mvn -B -q package -DskipTests
java -cp "target/classes" com.example.coloring.demo.ColoringDemo

echo
echo "VERIFY OK"
