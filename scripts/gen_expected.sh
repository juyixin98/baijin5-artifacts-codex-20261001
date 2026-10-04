#!/usr/bin/env bash
# Recompute the expected difference sets from the key lists using only
# coreutils (sort/comm) — deliberately independent of the Rust kernel,
# so the interop test compares against an outside reference.
set -euo pipefail
cd "$(dirname "$0")/.."

comm -23 <(sort -n fixtures/keys_a.txt) <(sort -n fixtures/keys_b.txt) > fixtures/expected_only_a.txt
comm -13 <(sort -n fixtures/keys_a.txt) <(sort -n fixtures/keys_b.txt) > fixtures/expected_only_b.txt

echo "expected_only_a: $(tr '\n' ' ' < fixtures/expected_only_a.txt)"
echo "expected_only_b: $(tr '\n' ' ' < fixtures/expected_only_b.txt)"
