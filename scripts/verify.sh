#!/usr/bin/env bash
# End-to-end evidence: native Go only, no containers, no extra runtimes.
# Clean full build, then three controlled edits (private implementation body,
# public inline constant, generic body) with minimal-invalidation evidence.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORK="$(mktemp -d)"
EVID="$ROOT/evidence"
mkdir -p "$EVID"
trap 'rm -rf "$WORK"' EXIT

PROJ="$WORK/shop"
mkdir -p "$PROJ/src" "$PROJ/cache"
cp examples/shop/src/pricing.rl examples/shop/src/report.rl examples/shop/src/app.rl "$PROJ/src/"
cat > "$PROJ/rlc.json" <<'JSON'
{
 "project":"shop","source_dir":"src","cache_dir":"cache",
 "entry_module":"app","entry_func":"main","redact_literals": false,
 "modules":[
  {"name":"pricing","file":"pricing.rl"},
  {"name":"report","file":"report.rl"},
  {"name":"app","file":"app.rl"}]
}
JSON

RLC="go run ./cmd/rlc"
SUM="go run ./cmd/evsum"

echo "[1/6] go vet + full test suite"
go vet ./...
go test ./... | tee "$EVID/00-go-test.txt"

echo "[2/6] clean full build"
$RLC build -config "$PROJ/rlc.json" -quiet > "$EVID/01-full-build.json" 2>"$EVID/01-full-build.diag"
go run ./cmd/evsum "$EVID/01-full-build.json" | tee "$EVID/01-full-build.summary"

echo "[3/6] baseline concrete result app.main(3); independently expected 362"
$RLC run -config "$PROJ/rlc.json" -int 3 | tee "$EVID/02-baseline-value.txt"

reset_and_build () {
  cp examples/shop/src/pricing.rl "$PROJ/src/pricing.rl"
  cp examples/shop/src/report.rl  "$PROJ/src/report.rl"
  cp examples/shop/src/app.rl     "$PROJ/src/app.rl"
  $RLC build -config "$PROJ/rlc.json" -quiet >/dev/null 2>&1
}

scenario () {
  local name="$1"
  echo "--- $name ---"
  go run ./cmd/evsum "$EVID/$name.json" | tee "$EVID/$name.summary"
  reset_and_build
}

echo "[4/6] scenario A: private implementation body change"
reset_and_build
sed -i '16s#return private_adjust(cents);#return private_adjust(cents) + 0;#' "$PROJ/src/pricing.rl"
$RLC build -config "$PROJ/rlc.json" -quiet > "$EVID/03-private-body.json" 2>"$EVID/03-private-body.diag"
scenario "03-private-body"

echo "[5/6] scenario B: public inline constant change (2 -> 3)"
sed -i 's#pub const BULK_FACTOR: int = 2;#pub const BULK_FACTOR: int = 3;#' "$PROJ/src/pricing.rl"
$RLC build -config "$PROJ/rlc.json" -quiet > "$EVID/04-inline-const.json" 2>"$EVID/04-inline-const.diag"
echo "    post-change app.main(3) independently expected 543"
$RLC run -config "$PROJ/rlc.json" -int 3 | tee "$EVID/04-inline-const.value"
scenario "04-inline-const"

echo "[6/6] scenario C: generic body change (explicit interface dependency)"
cat > "$PROJ/src/report.rl" <<'RL'
module report;
import pricing;
pub generic fn first(x: T) -> T {
	if (true == true) { return x; } else { return x; }
}
pub fn quote(cents: int, qty: int) -> int {
	let total: int = pricing::bulk_price(cents, qty);
	return first(total) + pricing::BULK_FACTOR;
}
RL
$RLC build -config "$PROJ/rlc.json" -quiet > "$EVID/05-generic-body.json" 2>"$EVID/05-generic-body.diag"
scenario "05-generic-body"

echo
echo "behavioral probes (external fixtures; expected results hand-authored)"
$RLC probe -config config/rlc.json -fixtures testdata/probes.json | tee "$EVID/06-probes.txt"

echo "OK: evidence written to $EVID"
