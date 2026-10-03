#!/usr/bin/env bash
# End-to-end CLI integration test driven through the shell: checks report
# content, request correlation, and concrete exit-code categories.
set -uo pipefail
cd "$(dirname "$0")/../.."

BIN=build/src/app/procrustes_fit
GEN=build/tools/gen_data/gen_data
fail=0
assert() { # description condition
  if eval "$2"; then
    echo "  ok  - $1"
  else
    echo "  FAIL- $1"
    fail=1
  fi
}

$GEN rigid2d data >/dev/null
$GEN similarity2d data >/dev/null
$GEN collinear2d data >/dev/null

echo "[cli] rigid2d happy path"
out=$("$BIN" --data data/rigid2d_pairs.csv --config config/rigid2d.ini 2>/tmp/cli_err1)
rc=$?
assert "exit code 0 on success" "[[ $rc -eq 0 ]]"
assert "report shows OK outcome" 'grep -q "outcome      : OK" <<<"$out"'
assert "report carries profile request id" 'grep -q "profile-rigid2d" <<<"$out"'
assert "report shows near-zero residual" "grep -qE \"weighted RMS residual : [0-9.e+-]*e-1[0-9]$\" <<<\"$out\""
assert "logs carry request id and version" 'grep -q "req=profile-rigid2d" /tmp/cli_err1 && grep -q "\[v1.0.0\]" /tmp/cli_err1'

echo "[cli] similarity2d via command-line flags"
out=$("$BIN" --data data/similarity2d_pairs.csv --mode similarity \
      --request-id cli-sim-1 2>/dev/null)
rc=$?
assert "exit code 0" "[[ $rc -eq 0 ]]"
assert "scale recovered as 1.75" 'grep -q "^scale        : 1.75" <<<"$out"'
assert "request id echoed" 'grep -q "cli-sim-1" <<<"$out"'

echo "[cli] collinear2d rotation-only vs reflection ambiguity"
out=$("$BIN" --data data/collinear2d_pairs.csv --reflection deny 2>/dev/null)
assert "rotation-only collinear is OK" 'grep -q "outcome      : OK" <<<"$out"'
out=$("$BIN" --data data/collinear2d_pairs.csv --reflection allow \
      --request-id cli-col-1 2>/dev/null)
rc=$?
assert "non-unique still exits 0" "[[ $rc -eq 0 ]]"
assert "non-unique outcome shown" 'grep -q "OK_WITH_UNCERTAINTY" <<<"$out"'
assert "uncertainty section present" 'grep -q "NON-UNIQUE" <<<"$out"'

echo "[cli] hard failures"
echo "not,a,valid,row,because" > /tmp/bad_pairs.csv
"$BIN" --data /tmp/bad_pairs.csv --request-id cli-bad-1 >/tmp/cli_bad_out 2>/tmp/cli_bad_err
rc=$?
assert "malformed CSV exits 2" "[[ $rc -eq 2 ]]"
assert "failure logged with request id" 'grep -q "req=cli-bad-1" /tmp/cli_bad_err'
assert "failure severity recorded" 'grep -q "\[FAIL\]" /tmp/cli_bad_err'

"$BIN" --data data/rigid2d_pairs.csv --mode bogus >/dev/null 2>/tmp/cli_mode_err
rc=$?
assert "invalid mode exits 2" "[[ $rc -eq 2 ]]"

if [[ $fail -ne 0 ]]; then
  echo "[cli] integration tests FAILED"
  exit 1
fi
echo "[cli] integration tests PASSED"
