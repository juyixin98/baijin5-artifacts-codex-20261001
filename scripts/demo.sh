#!/usr/bin/env bash
# End-to-end local demonstration: fixtures -> solve -> eval -> serve -> bench.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build"
[[ -x "$BUILD/pade_cli" ]] || bash "$ROOT/scripts/build.sh" "$BUILD"
RUN_ID="demo-$(date +%s)"
export PADE_RUN_ID="$RUN_ID"
echo "### [1/5] generate synthetic fixtures"
"$BUILD/gen_fixtures" "$ROOT/data"
echo "### [2/5] exp12 Pade [4/4] (run_id=$RUN_ID)"
set +e
"$BUILD/pade_cli" solve --file "$ROOT/data/exp12.series" --m 4 --n 4 --run-id "$RUN_ID"
RC=$?
set -e
echo "(exit=$RC; 0=ok)"
echo "### [3/5] degeneracy: x3_factor5 f=x^3 at [1/2] -> NORMALIZATION_IMPOSSIBLE"
set +e
"$BUILD/pade_cli" solve --file "$ROOT/data/x3_factor5.series" --m 1 --n 2 --run-id "$RUN_ID"
echo "(exit=$?; 11=normalization impossible)"
set -e
echo "### [4/5] eval geometric [1/1] at x=0.5 and x=1 (pole)"
"$BUILD/pade_cli" eval --file "$ROOT/data/geometric10.series" --m 1 --n 1 --x 0.5 --run-id "$RUN_ID"
set +e
"$BUILD/pade_cli" eval --file "$ROOT/data/geometric10.series" --m 1 --n 1 --x 1 --run-id "$RUN_ID"
echo "(exit=$?; 20=pole evaluated)"
set -e
echo "### [5/5] stdin line service"
printf 'SOLVE %s 3 3\nEVAL %s 3 3 0.3\nQUIT\n' \
  "$ROOT/data/exp12.series" "$ROOT/data/exp12.series" |
  "$BUILD/pade_cli" serve --run-id "$RUN_ID-srv"
echo "### benchmark"
"$BUILD/pade_bench" | column -t -s,
