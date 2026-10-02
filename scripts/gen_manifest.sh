#!/usr/bin/env bash
# Regenerate fixtures/generated/MANIFEST.sha256 from the generated CSVs.
# Run after gen_fixtures. Pure shell + sha256sum, no extra runtime.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/fixtures/generated"
OUT="$DIR/MANIFEST.sha256"
: > "$OUT"
for f in "$DIR"/*.csv; do
  ( cd "$DIR" && sha256sum "$(basename "$f")" >> MANIFEST.sha256 )
done
echo "[gen_manifest] wrote $OUT:" >&2
cat "$OUT" >&2
