#!/usr/bin/env bash
# End-to-end CLI smoke test: exact + field + duplicate points + mixed-mode
# rejection + log presence. Arguments: POLYEVAL_BIN PROJECT_ROOT
set -uo pipefail
BIN="$1"; ROOT="$2"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LOG="$TMP/run.jsonl"

"$BIN" eval --request "$ROOT/examples/request_demo.txt" \
            --config "$ROOT/configs/default.conf" \
            --log "$LOG" --explain > "$TMP/out.jsonl" 2> "$TMP/err.txt"
RC=$?
if [ $RC -ne 1 ]; then echo "expected exit 1 (one rejected request), got $RC"; exit 1; fi

grep -q '"id":"demo-exact"' "$TMP/out.jsonl" || { echo missing exact; exit 1; }
grep -q '"y":"34"' "$TMP/out.jsonl" || { echo missing concrete value 34; exit 1; }
grep -q '"id":"demo-field"' "$TMP/out.jsonl" || { echo missing field; exit 1; }
grep -q '"status":"MIXED_MODE"' "$TMP/out.jsonl" || { echo missing mixed-mode failure; exit 1; }
# request order preserved in output
head -1 "$TMP/out.jsonl" | grep -q 'demo-exact' || { echo order broken; exit 1; }
# duplicate points produced identical values
grep -q '"UNCERTAIN"' "$TMP/out.jsonl" && { echo unexpected uncertainty; exit 1; }
# log relates identity + version + phase
grep -q '"version":"1.0.0"' "$LOG" || { echo log version missing; exit 1; }
grep -q '"request_id":"demo-field"' "$LOG" || { echo log id missing; exit 1; }
grep -q '"phase":"crosscheck"' "$LOG" || { echo log phase missing; exit 1; }
echo "cli_smoke_ok"
