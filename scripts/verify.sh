#!/usr/bin/env bash
# Full verification: build, test suite, and CLI example runs.
# All logs and JSON reports are kept under out/ for replay and audit.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CARGO="${CARGO:-$HOME/.cargo/bin/cargo}"
CLI="$ROOT/target/debug/qecli"
OUT="$ROOT/out"
mkdir -p "$OUT"

SUMMARY="$OUT/summary.log"
: > "$SUMMARY"
FAILED=0

note() {
    echo "$*" | tee -a "$SUMMARY"
}

# step <name> <expected_exit> <cmd...>
step() {
    local name="$1" want="$2"
    shift 2
    "$@" > "$OUT/$name.log" 2>&1
    local rc=$?
    if [ "$rc" -eq "$want" ]; then
        note "ok    $name (exit=$rc, log=out/$name.log)"
    else
        note "FAIL  $name (exit=$rc, expected $want, log=out/$name.log)"
        FAILED=1
    fi
}

note "== build =="
step 01_build 0 "$CARGO" build --locked

note "== test suite =="
step 02_test 0 "$CARGO" test --locked

note "== cli examples: normal runs =="
step 03_cli_pipeline 0 "$CLI" \
    --model fixtures/model_two.json \
    --formula fixtures/formulas.json --name alt_forall_exists \
    --mode pipeline
step 04_cli_eval_empty_domain 0 "$CLI" \
    --model fixtures/model_empty.json \
    --formula fixtures/formulas.json --name vacuous_forall \
    --mode eval
step 05_cli_qe_budget_partial 0 "$CLI" \
    --model fixtures/model_two.json \
    --formula fixtures/formulas.json --name collide_names \
    --mode qe --budget 1

note "== cli examples: failure categories =="
step 06_cli_err_state_conflict 3 "$CLI" \
    --model fixtures/model_empty_forbidden.json \
    --formula fixtures/formulas.json --name vacuous_forall
step 07_cli_err_invalid_input 2 "$CLI" \
    --model fixtures/model_two.json \
    --formula fixtures/formulas.json --name no_such_formula
step 08_cli_err_missing_file 2 "$CLI" \
    --model fixtures/missing.json \
    --formula fixtures/formulas.json --name vacuous_forall

if [ "$FAILED" -eq 0 ]; then
    note "RESULT: all steps passed"
else
    note "RESULT: failures present, see logs above"
fi
exit "$FAILED"
