#!/usr/bin/env bash
# Local verification entry point.  Exits non-zero if any step fails.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== runtime versions =="
python3 - <<'PY'
import sys
sys.path.insert(0, "src")
from watershed_backend.version import runtime_versions
for name, version in runtime_versions().items():
    print(f"  {name}: {version}")
PY

echo "== test suite =="
python3 -m pytest -v

echo "== expected judgement =="
echo "PASS  <=> exit code 0 and every test above reports PASSED"
echo "FAIL  <=> any FAILED/ERROR line; the failing assertion names the"
echo "         fixture and the violated behaviour or failure category"
