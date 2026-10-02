#!/usr/bin/env bash
#
# verify.sh — local, offline verification for the hpacklab multi-module
# repository. It builds, vets, tests (race + coverage) every module and
# prints an explicit PASS/FAIL tally. No network access is required; all
# dependencies are expected in the local Go module cache.
#
# Usage: ./scripts/verify.sh
#
# Exit status is non-zero if any step fails.
set -u
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULES=(codec hpack compat service itest)

# Force offline module resolution so a missing dependency is a hard failure
# rather than a silent download. We disable the root go.work for per-module
# verification so each module resolves through its own go.mod/go.sum, which
# allows -mod=mod (workspace mode forbids it).
export GOFLAGS="-mod=mod"
export GOWORK="off"
export GOPROXY="off"
export GOTOOLCHAIN="local"

PASS=0
FAIL=0
FAILED_STEPS=()

run_step() {
  local label="$1"; shift
  printf '\n\033[1;36m== %s ==\033[0m\n' "$label"
  if "$@"; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1))
    FAILED_STEPS+=("$label")
  fi
}

cd "$ROOT"

# 0. Toolchain presence.
echo "toolchain: $(go version)"

# 1. gofmt check across all modules.
check_gofmt() {
  local bad
  bad="$(find modules -name '*.go' -not -path '*/testdata/*' -print0 \
        | xargs -0 gofmt -l)"
  if [[ -n "$bad" ]]; then
    echo "gofmt differences in:"
    echo "$bad"
    return 1
  fi
  echo "all Go files gofmt-clean"
}
run_step "gofmt" check_gofmt

# 2. Per-module build, vet and tests.
for m in "${MODULES[@]}"; do
  dir="$ROOT/modules/$m"
  (cd "$dir" && go build ./...) || { FAIL=$((FAIL+1)); FAILED_STEPS+=("$m/build"); continue; }
  run_step "$m: vet"        bash -c "cd '$dir' && go vet ./..."
  run_step "$m: test+race"  bash -c "cd '$dir' && go test -race -count=1 ./..."
  run_step "$m: coverage"   bash -c "cd '$dir' && go test -cover -count=1 ./..."
done

# 3. Service smoke test: boot the binary on an ephemeral DB, hit it through a
#    tiny raw h2c exchange is covered by the itest module; here we only verify
#    the CLI starts and answers 'list' on an empty database.
smoke_cli() {
  local tmp; tmp="$(mktemp -d)"
  local bin="$tmp/hpackd"
  (cd "$ROOT/modules/service" && go build -o "$bin" ./cmd/hpackd) || return 1
  cat > "$tmp/cfg.json" <<JSON
{
  "server": {"listen": "127.0.0.1:0"},
  "storage": {"sqlite_path": "$tmp/smoke.db"}
}
JSON
  "$bin" list -c "$tmp/cfg.json" >"$tmp/list.out" 2>&1 || { cat "$tmp/list.out"; return 1; }
  grep -q "summary" "$tmp/list.out" || { cat "$tmp/list.out"; return 1; }
  echo "CLI list works on an empty database"
  rm -rf "$tmp"
}
run_step "service CLI smoke" smoke_cli

echo
echo "================ SUMMARY ================"
echo "passed steps: $PASS"
echo "failed steps: $FAIL"
if (( FAIL > 0 )); then
  printf 'FAILED: %s\n' "${FAILED_STEPS[*]}"
  exit 1
fi
echo "ALL CHECKS PASSED"
