#!/usr/bin/env bash
# Run the validation suite against the on-disk fixtures and write
# reports/validation_report.json.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m app.validation
