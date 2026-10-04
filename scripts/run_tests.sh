#!/usr/bin/env bash
# Run the test suite with a correlatable run identity, persist full output and
# JUnit XML so failures/skips are retained rather than scrolled away.
set -euo pipefail
cd "$(dirname "$0")/.."

RUN_ID="test-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="logs/test-runs"
mkdir -p "${OUT_DIR}"
LOG="${OUT_DIR}/${RUN_ID}.log"
XML="${OUT_DIR}/${RUN_ID}.junit.xml"

echo "RUN_ID=${RUN_ID}"
echo "log:    ${LOG}"
echo "junit:  ${XML}"

set +e
python3 -m pytest tests/ -p no:cacheprovider \
  --junitxml="${XML}" --cov=app --cov-report=term-missing "$@" 2>&1 | tee "${LOG}"
status=${PIPESTATUS[0]}
set -e

echo "${status}" > "${OUT_DIR}/${RUN_ID}.exitcode"
echo "RUN_ID=${RUN_ID} exit=${status}"
exit "${status}"
