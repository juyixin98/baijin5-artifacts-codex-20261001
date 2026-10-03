#!/usr/bin/env bash
# Full local verification: deps -> build -> ctest -> independent reference
# checks -> end-to-end demo verdicts. Native processes only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTEST="$ROOT/deps/cmake-3.28.3-linux-x86_64/bin/ctest"

"$ROOT/scripts/fetch_deps.sh"
"$ROOT/scripts/build.sh"
( cd "$ROOT/build" && "$CTEST" --output-on-failure )

echo "== independent long-double reference: exp, degree 40"
"$ROOT/build/bench_ref" exp 40 | tail -2
echo "== independent long-double reference: |x| (non-smooth)"
"$ROOT/build/bench_ref" abs 64 | tail -1

echo "== demo: exp strict profile (expect accepted, exit 0)"
set +e
"$ROOT/build/cheb_demo" exp 40 30 -1 1 "$ROOT/config/tolerance_strict.conf"
E1=$?
echo "== demo: |x| strict profile (expect rejected, exit 1)"
"$ROOT/build/cheb_demo" abs 64 64 -1 1 "$ROOT/config/tolerance_strict.conf"
E2=$?
echo "== demo: |x| relaxed profile (expect indeterminate, exit 0)"
"$ROOT/build/cheb_demo" abs 64 16 -1 1 "$ROOT/config/tolerance_relaxed.conf"
E3=$?
set -e
[ "$E1" -eq 0 ] || { echo "exp demo failed" >&2; exit 1; }
[ "$E2" -eq 1 ] || { echo "abs strict demo did not reject" >&2; exit 1; }
[ "$E3" -eq 0 ] || { echo "abs relaxed demo crashed" >&2; exit 1; }
echo "ALL VERIFICATION STEPS COMPLETED"
