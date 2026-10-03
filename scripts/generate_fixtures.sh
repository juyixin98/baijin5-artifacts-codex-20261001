#!/usr/bin/env bash
# Regenerate the synthetic fixtures (fixtures/*.png + manifest.json).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m app.fixtures
