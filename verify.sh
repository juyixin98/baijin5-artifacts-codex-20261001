#!/usr/bin/env bash
# Local verification entry point for the rescheck project.
# Builds, runs the full test suite, regenerates the synthetic fixture,
# and checks the CLI verdict + exit code for every fixture proof.
#
# Usage: ./verify.sh
# Expected: every line reports OK and the script exits 0.

set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/4 build =="
cargo build --all-targets

echo "== 2/4 unit + integration tests =="
cargo test

echo "== 3/4 regenerate synthetic fixture =="
cargo run -q --bin gen_fixtures

echo "== 4/4 CLI verdicts on fixtures =="

# name expected_exit expected_verdict
cases="
valid_simple 0 verified
valid_with_deletion 0 verified
generated_chain 0 verified
tampered_resolvent 1 rejected
dangling_parent 1 rejected
deleted_parent 1 rejected
duplicate_literal 1 rejected
no_empty_clause 2 unverified
"

fail=0
while read -r name want_exit want_verdict; do
    [ -z "$name" ] && continue
    set +e
    out=$(target/debug/rescheck "fixtures/$name.proof" --request-id "verify:$name")
    got_exit=$?
    set -e
    got_verdict=$(printf '%s' "$out" | grep -o '"verdict": "[a-z]*"' | cut -d'"' -f4)
    if [ "$got_exit" = "$want_exit" ] && [ "$got_verdict" = "$want_verdict" ]; then
        echo "OK   $name: exit=$got_exit verdict=$got_verdict"
    else
        echo "FAIL $name: exit=$got_exit (want $want_exit) verdict=$got_verdict (want $want_verdict)"
        fail=1
    fi
done <<< "$cases"

# Resource-limit behavior through the CLI: tight step budget => unverified (exit 2).
set +e
target/debug/rescheck fixtures/generated_chain.proof --max-steps 100 > /dev/null
limited_exit=$?
set -e
if [ "$limited_exit" = "2" ]; then
    echo "OK   generated_chain --max-steps 100: exit=2 (unverified)"
else
    echo "FAIL generated_chain --max-steps 100: exit=$limited_exit (want 2)"
    fail=1
fi

if [ "$fail" != "0" ]; then
    echo "verification FAILED"
    exit 1
fi
echo "verification complete"
