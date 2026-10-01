#!/usr/bin/env bash
# Reproducible verification for fologic. Native Linux process only; no containers.
# Steps: fmt check, build, unit+integration tests, regenerate synthetic fixtures,
# then normal AND abnormal CLI runs with results kept under a run-id directory.
set -u
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CARGO="${CARGO:-$HOME/.cargo/bin/cargo}"
if ! command -v "$CARGO" >/dev/null 2>&1; then CARGO="$(command -v cargo)"; fi

RUN_ID="verify-$(date +%Y%m%d-%H%M%S)-$$"
OUT="fixtures/results/$RUN_ID"
mkdir -p "$OUT"
LOG="$OUT/run.log"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

log "run_id=$RUN_ID out=$OUT"
log "toolchain: $($CARGO --version)"

log "cargo fmt --check"
if "$CARGO" fmt --all -- --check >>"$LOG" 2>&1; then
  log "fmt ok"
else
  log "fmt reported differences (continuing; run 'cargo fmt --all' to fix)"
fi

log "cargo build"
"$CARGO" build --all-targets >>"$LOG" 2>&1

log "regenerate synthetic fixtures"
"$CARGO" run --quiet --example gen_fixtures >>"$LOG" 2>&1

log "cargo test"
"$CARGO" test --workspace 2>&1 | tee "$OUT/cargo-test.txt"

# CLI matrix: every fixture (success and failure). Exit codes are intentionally
# not failing the script for non-zero cases; we persist stdout/stderr per case.
NORMAL=(
  req_success_f03 req_success_f04 req_success_shadow
  req_empty_allowed req_empty_exists_allowed
  req_budget_mid req_budget_zero req_budget_exact
)
ABNORMAL=(
  req_empty_denied req_node_cap req_unknown_symbol req_arity req_unbound req_free_var
)

run_case() {
  local name="$1"
  local req="fixtures/requests/$name.json"
  log "cli $name"
  set +e
  "$CARGO" run --quiet -- check-file "$req" >"$OUT/$name.out.json" 2>"$OUT/$name.err.json"
  local code=$?
  set -e
  echo "$code" >"$OUT/$name.exitcode"
  log "  exit=$code -> $OUT/$name.(out|err).json"
}

for c in "${NORMAL[@]}" "${ABNORMAL[@]}"; do
  run_case "$c"
done

# Produce a compact matrix summary for human review.
python3 - "$OUT" <<'PY' 2>/dev/null | tee "$OUT/summary.txt" || true
import json, os, sys, glob
out = sys.argv[1]
rows = []
for ec in sorted(glob.glob(os.path.join(out, "*.exitcode"))):
    name = os.path.basename(ec).replace(".exitcode", "")
    code = open(ec).read().strip()
    body = None
    for suffix in ("out.json", "err.json"):
        p = os.path.join(out, f"{name}.{suffix}")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            try:
                body = json.load(open(p))
                break
            except Exception:
                pass
    if body is None:
        verdict = "-"
    elif "error" in body:
        verdict = f"ERROR/{body['error']['kind']}/{body['error']['code']}"
    else:
        verdict = f"{body['verdict']} used={body['instantiations_used']} resid={body['residual_quantifiers']}"
    rows.append((name, code, verdict))
w = max(len(r[0]) for r in rows)
print(f"{'case'.ljust(w)}  exit  result")
for name, code, verdict in rows:
    print(f"{name.ljust(w)}  {code:>4}  {verdict}")
PY

log "artifacts stored at $OUT"
log "DONE"
echo
echo "Results directory: $OUT"
