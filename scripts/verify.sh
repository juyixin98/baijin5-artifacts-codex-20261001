#!/usr/bin/env bash
# Native Linux verification entry point: builds, runs the full JUnit suite and
# exercises the CLI on odd cycle, complete graph, disconnected graph and the
# budget-stop path. Requires OpenJDK 21 and Maven on PATH; no containers.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== [1/3] clean build + full JUnit suite (includes n<=6 exhaustive oracle) =="
mvn -B clean test

echo "== [2/3] package =="
mvn -B -q package -DskipTests
JAR="target/vertex-coloring-1.0.0.jar"

echo "== [3/3] CLI smoke checks =="
run() { echo "---- $*"; java -jar "$JAR" "$@"; }

run cycle:5
run complete:6
run file:src/test/resources/fixtures/k3-plus-k2.graph
run cycle:9 1          # budget stop: only proven bounds may be reported
run file:src/test/resources/fixtures/grotzsch.graph

echo
echo "VERIFY_OK: build, 64 tests, CLI cases (incl. budget stop) all succeeded."
