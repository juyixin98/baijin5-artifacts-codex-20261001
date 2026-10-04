#!/usr/bin/env bash
# Full local verification: build, test, regenerate fixtures, solve every example.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/4 build =="
mvn -q package

echo "== 2/4 tests =="
mvn -q test
echo "tests: OK"

echo "== 3/4 fixtures =="
java -jar mwis-cli/target/mwis-cli-1.0.0.jar generate examples

echo "== 4/4 example runs =="
status=0
for f in examples/request-*.json; do
  name="$(basename "$f")"
  if out="$(java -jar mwis-cli/target/mwis-cli-1.0.0.jar solve "$f" 2>/dev/null)"; then
    code=0
  else
    code=$?
  fi
  svc_status="$(printf '%s' "$out" | grep -m1 '"status"' | sed 's/[^A-Z_]//g')"
  weight="$(printf '%s' "$out" | grep -m1 '"weight"' | tr -dc '0-9-' || true)"
  printf '  %-40s exit=%d status=%-22s weight=%s\n' "$name" "$code" "$svc_status" "${weight:-none}"
  # broken fixtures are expected to fail with non-zero exit codes
  case "$name" in
    *broken*|*tight-budget*) [ "$code" -ne 0 ] || status=1 ;;
    *)                       [ "$code" -eq 0 ] || status=1 ;;
  esac
done
[ "$status" -eq 0 ] && echo "verify: OK" || { echo "verify: FAILED"; exit 1; }
