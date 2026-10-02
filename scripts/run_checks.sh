#!/usr/bin/env bash
# Local verification entry point.
# Usage: ./scripts/run_checks.sh
# Expected: all tests pass, coverage >= 80% on the app package.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== dependency versions =="
python3 - <<'PY'
import platform
import fastapi, numpy, scipy, PIL, pydantic
print("python ", platform.python_version())
print("numpy  ", numpy.__version__)
print("scipy  ", scipy.__version__)
print("pillow ", PIL.__version__)
print("fastapi", fastapi.__version__)
print("pydantic", pydantic.__version__)
PY

echo "== test suite with coverage =="
python3 -m pytest --cov=app --cov-report=term-missing "$@"
