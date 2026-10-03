#!/usr/bin/env bash
# End-to-end verification for the RL restricted-language module.
# Native Linux + Go toolchain only. No containers, no extra runtimes.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BIN="$(mktemp -d)/rlverify"
trap 'rm -rf "$(dirname "$BIN")"' EXIT

pass=0
fail=0

info() { printf '\n=== %s ===\n' "$1"; }
check() {
  # check <description> <0|1 expected-ok> -- <command...>
  local desc="$1"; local want="$2"; shift 2
  if [ "$1" = "--" ]; then shift; fi
  if "$@" >/tmp/rlverify.out 2>/tmp/rlverify.err; then
    got=0
  else
    got=1
  fi
  if [ "$got" = "$want" ]; then
    printf 'PASS  %s\n' "$desc"; pass=$((pass+1))
  else
    printf 'FAIL  %s (want exit %s got %s)\n' "$desc" "$want" "$got"; fail=$((fail+1))
    sed 's/^/      /' /tmp/rlverify.out /tmp/rlverify.err
  fi
}

contains() { # contains <file> <text>
  if grep -qF "$2" "$1"; then
    printf 'PASS  output contains %s\n' "$2"; pass=$((pass+1))
  else
    printf 'FAIL  output missing %s\n' "$2"; fail=$((fail+1))
    sed 's/^/      /' "$1"
  fi
}

notcontains() {
  if grep -qF "$2" "$1"; then
    printf 'FAIL  output unexpectedly contains %s\n' "$2"; fail=$((fail+1))
    sed 's/^/      /' "$1"
  else
    printf 'PASS  output hides %s\n' "$2"; pass=$((pass+1))
  fi
}

info "build & vet"
go build ./... && echo "PASS  go build" && pass=$((pass+1)) || { echo "FAIL  go build"; fail=$((fail+1)); }
go vet ./... && echo "PASS  go vet" && pass=$((pass+1)) || { echo "FAIL  go vet"; fail=$((fail+1)); }
go build -o "$BIN" ./cmd/rlverify && echo "PASS  rlverify binary" && pass=$((pass+1)) || { echo "FAIL  binary"; fail=$((fail+1)); }

info "unit + golden tests"
if go test ./...; then pass=$((pass+1)); else fail=$((fail+1)); fi

info "execution results (synthetic fixtures)"
"$BIN" run --dir testdata/scenarios/inline-const_old > /tmp/o1.txt 2>&1
contains /tmp/o1.txt 405
"$BIN" run --dir testdata/scenarios/inline-const_new > /tmp/o2.txt 2>&1
contains /tmp/o2.txt 445
"$BIN" run --dir testdata/scenarios/generic-body_new > /tmp/o3.txt 2>&1
contains /tmp/o3.txt 405
"$BIN" run --dir testdata/scenarios/private-body_old > /tmp/o4.txt 2>&1
contains /tmp/o4.txt 405
"$BIN" run --dir testdata/scenarios/private-body_new > /tmp/o5.txt 2>&1
contains /tmp/o5.txt 406

info "private body change -> minimal invalidation is only the private unit"
"$BIN" diff --old testdata/scenarios/private-body_old --new testdata/scenarios/private-body_new > /tmp/d1.txt 2>&1
contains /tmp/d1.txt "private_body_only core.secret"
not_contains_invalid() {
  if grep -E '^  X ' /tmp/d1.txt | grep -qF "$1"; then
    echo "FAIL  $1 must not be invalidated"; fail=$((fail+1))
  else
    echo "PASS  $1 not invalidated by private body"; pass=$((pass+1))
  fi
}
not_contains_invalid "core.Calc"
not_contains_invalid "app.Run"

info "inline const change -> propagates through inlining chain"
"$BIN" diff --old testdata/scenarios/inline-const_old --new testdata/scenarios/inline-const_new > /tmp/d2.txt 2>&1
contains /tmp/d2.txt "inline_const core.Offset"
contains /tmp/d2.txt "X app.Run"
contains /tmp/d2.txt "X core.Calc"

info "generic body change -> only used instantiation propagates"
"$BIN" diff --old testdata/scenarios/generic-body_old --new testdata/scenarios/generic-body_new > /tmp/d3.txt 2>&1
contains /tmp/d3.txt "generic_body core.Scale[int]"
contains /tmp/d3.txt "X app.Run"

info "public type change -> signature rejection"
"$BIN" diff --old testdata/scenarios/public-type_old --new testdata/scenarios/public-type_new > /tmp/d4.txt 2>&1
contains /tmp/d4.txt "public_signature core.Tag"
contains /tmp/d4.txt "removed core.Count"

info "old fingerprints cannot be reused across compile semantic versions"
"$BIN" diff --old testdata/scenarios/version-old --new testdata/scenarios/version-new \
  --semver-old 1.9.0 --semver-new 2.0.0 > /tmp/d5.txt 2>&1
contains /tmp/d5.txt "version_gate INCONCLUSIVE"
contains /tmp/d5.txt "reused 0"
"$BIN" diff --old testdata/scenarios/version-old --new testdata/scenarios/version-new \
  --semver-old 1.9.0 --semver-new 1.10.0 > /tmp/d6.txt 2>&1
contains /tmp/d6.txt "version_gate OK"

info "compiler failure categories"
check "unknown import rejected" 1 -- "$BIN" compile --dir testdata/scenarios/errors
"$BIN" compile --dir testdata/scenarios/errors 2>/tmp/e1.txt; contains /tmp/e1.txt "[import_missing]"
"$BIN" compile --dir testdata/scenarios/errors2 2>/tmp/e2.txt; contains /tmp/e2.txt "[unknown_symbol]"
"$BIN" compile --dir testdata/scenarios/errors3 2>/tmp/e3.txt; contains /tmp/e3.txt "[type_mismatch]"
"$BIN" run --dir testdata/scenarios/errors4 2>/tmp/e4.txt; contains /tmp/e4.txt "division_by_zero"

info "sensitive value masking and correlation diagnostics"
"$BIN" compile --dir testdata/scenarios/sensitive > /tmp/s1.txt 2>&1
contains /tmp/s1.txt "<redacted>"
notcontains /tmp/s1.txt "SECRET-1234567890"
"$BIN" diff --old testdata/scenarios/inline-const_old --new testdata/scenarios/inline-const_new \
  --diag --request-id verify-run-42 2>/tmp/s2.txt >/dev/null
contains /tmp/s2.txt '"request_id":"verify-run-42"'
contains /tmp/s2.txt '"record_id":"verify-run-42-r01"'
contains /tmp/s2.txt '"decision":"reject"'

info "config-driven aggregate verification"
"$BIN" verify --config config/rlverify.json
if [ $? -eq 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); fi

printf '\n==============================\n'
printf 'RESULT: %d passed, %d failed\n' "$pass" "$fail"
printf '==============================\n'
[ "$fail" -eq 0 ]
