#!/usr/bin/env bash
# Full verification: run the test suite and print the surefire summary.
set -euo pipefail
cd "$(dirname "$0")/.."

mvn test
echo "---- surefire summary ----"
grep -h "Tests run" target/surefire-reports/*.txt
echo "ALL TESTS PASSED"
