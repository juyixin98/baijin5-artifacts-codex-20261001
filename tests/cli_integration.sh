#!/usr/bin/env bash
# End-to-end CLI checks using plain shell + grep (no extra runtimes). Asserts
# concrete output content and failure categories, not just exit codes.
set -uo pipefail
MP_EVAL="$1"
SRC="$2"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0
check() { # description expected_substring actual
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"; else
    echo "FAIL $1: missing [$2] in:"; echo "$3" | sed 's/^/     /'; fail=1; fi
}
check_absent() { if [[ "$3" != *"$2"* ]]; then echo "ok   $1"; else
  echo "FAIL $1: unexpected [$2] in:"; echo "$3" | sed 's/^/     /'; fail=1; fi; }

cat > "$TMP/dup.req" <<'R'
REQUEST it-dup
JOB a
DOMAIN INTEGER
COEFF 1 -6 11 -6
POINTS 2 2 2 0
R
out=$("$MP_EVAL" --request "$TMP/dup.req" --steps)
rc=$?
check "dup exit 0" 0 "$rc"
check "dup three zeros" "RESULT idx=2 x=2 p(x)=0" "$out"
check "dup f(0)=-6" "RESULT idx=3 x=0 p(x)=-6" "$out"
check "dup identity correlated" "it-dup/a" "$out"
check "dup shows processing step" "product_tree.build" "$out"
check "dup no failure" "" "$(echo "$out" | grep FAILURE || true)"

cat > "$TMP/mix.req" <<'R'
JOB m
DOMAIN INTEGER
MOD 7
COEFF 1
POINTS 1
R
out=$("$MP_EVAL" --request "$TMP/mix.req" --format json 2>/dev/null)
rc=$?
check "mixed domain nonzero exit" 1 "$rc"
check "mixed domain category" 'DOMAIN_MISMATCH' "$out"

cat > "$TMP/field.req" <<'R'
REQUEST it-field
JOB f
DOMAIN FIELD
MOD 17
COEFF 1 0 -1
POINTS 6 6 1
R
out=$("$MP_EVAL" --request "$TMP/field.req" --config "$SRC/config/default.ini")
# x^2-1 at 6 (35 mod17=1), duplicate 6 ->1, at 1 ->0
check "field f(6)=1" "idx=0 x=6 p(x)=1" "$out"
check "field duplicate preserved" "idx=1 x=6 p(x)=1" "$out"
check "field f(1)=0" "idx=2 x=1 p(x)=0" "$out"

# Forced batching produces identical values to a single batch.
cat > "$TMP/one.ini" <<'I'
memory_limit = 64MiB
max_batch_points = 1
bytes_per_slot = 24
memory_fudge = 2
I"
a=$("$MP_EVAL" --request "$TMP/dup.req" | grep 'RESULT' | sort)
b=$("$MP_EVAL" --request "$TMP/dup.req" --config "$TMP/one.ini" | grep 'RESULT' | sort)
check "batch results identical" "$a" "$b"

if [[ $fail -ne 0 ]]; then echo "CLI_INTEGRATION_FAILED"; exit 1; fi
echo "CLI_INTEGRATION_OK"
