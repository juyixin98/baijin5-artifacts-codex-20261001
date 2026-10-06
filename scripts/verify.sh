#!/usr/bin/env bash
# End-to-end verification: build, unit/integration tests, then CLI
# runs against the JSON fixtures with asserted outcomes.
# Usage: bash scripts/verify.sh   (from the repository root)
set -u
cd "$(dirname "$0")/.."

BIN=target/debug/dmc
OUT=target/verify
mkdir -p "$OUT"
pass=0
fail=0

ok()  { pass=$((pass+1)); echo "PASS  $1"; }
bad() { fail=$((fail+1)); echo "FAIL  $1"; }

echo "== cargo build =="
cargo build 2>&1 | tail -1 || { bad "cargo build"; }

echo "== cargo test =="
if cargo test 2>&1 | tee "$OUT/cargo-test.log" | grep -q "test result: FAILED"; then
    bad "cargo test"
else
    ok "cargo test"
fi

# expect_accept <fixture> <expected-count> [extra args...]
expect_accept() {
    local fixture="$1" want="$2"; shift 2
    local out
    out=$($BIN count --request "$fixture" --config config/default.json \
          --proof "$OUT/$(basename "$fixture" .json).proof.json" "$@" 2>&1)
    if echo "$out" | grep -q '"category":"accepted"' && echo "$out" | grep -q "count=$want"; then
        ok "$fixture accepted with count=$want"
    else
        bad "$fixture (want count=$want): $out"
    fi
}

# expect_reject <fixture> <expected-category> [extra args...]
expect_reject() {
    local fixture="$1" want="$2"; shift 2
    local out
    out=$($BIN count --request "$fixture" "$@" 2>&1)
    if echo "$out" | grep -q "\"category\":\"$want\""; then
        ok "$fixture rejected as $want"
    else
        bad "$fixture (want $want): $out"
    fi
}

echo "== CLI fixture runs =="
expect_accept fixtures/req_or_split.json 3
expect_accept fixtures/req_and_disjoint.json 6
expect_accept fixtures/req_big_smooth.json 1267650600228229401496703205376
expect_reject fixtures/req_and_overlap.json and_not_decomposable --config config/default.json
expect_reject fixtures/req_nondet_or.json or_not_deterministic --config config/default.json
expect_reject fixtures/req_undecidable.json undecidable --config fixtures/config_strict.json

# Proof record sanity: total and smoothing exponent must be present.
if grep -q '"total": "1267650600228229401496703205376"' "$OUT/req_big_smooth.proof.json" \
   && grep -q '"top_smooth_exp": 100' "$OUT/req_big_smooth.proof.json"; then
    ok "proof record for req_big_smooth carries total and smoothing exponent"
else
    bad "proof record for req_big_smooth"
fi

# Sensitive label must not leak into CLI output.
leak=$($BIN count --request fixtures/req_or_split.json --config config/default.json)
if echo "$leak" | grep -q "demo-sensitive-label"; then
    bad "sensitive label leaked into diagnostics"
else
    ok "sensitive label redacted in diagnostics"
fi

echo "== summary: $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
