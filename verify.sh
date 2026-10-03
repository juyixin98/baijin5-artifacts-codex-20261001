#!/usr/bin/env bash
# Reproducible native verification for the mylnk project.
# Requires Go 1.22 on PATH. Uses no containers and no extra runtimes.
set -euo pipefail

cd "$(dirname "$0")"
RUN_ID="verify-$(date +%Y%m%d-%H%M%S)-$$"
export MYLNK_RUN_ID="$RUN_ID"

echo "==> run id: $RUN_ID"
echo "==> go version: $(go version)"

echo "==> go mod verify"
go mod verify

echo "==> go vet ./..."
go vet ./...

echo "==> unit + integration tests (race detector)"
go test ./... -count=1 -race

echo "==> regenerate reproducible fixtures"
go run ./cmd/genfixtures . >/dev/null

echo "==> semantic differential from .asm sources (independent oracle)"
go run ./cmd/mylnk diff -from-asm -asmdir fixtures/asm -spec fixtures/expectations.txt

echo "==> semantic differential from committed .mkobj objects"
go run ./cmd/mylnk diff -from-asm=false -objdir fixtures/gen -spec fixtures/expectations.txt

echo "==> manual link/run spot checks"
go run ./cmd/mylnk run -entry main fixtures/gen/ws_main.mkobj fixtures/gen/ws_strong.mkobj fixtures/gen/ws_weak.mkobj
go run ./cmd/mylnk run -entry main fixtures/gen/circ_main.mkobj fixtures/gen/circ_even.mkobj fixtures/gen/circ_odd.mkobj

echo "==> undefined symbol diagnostic (expected to fail)"
if go run ./cmd/mylnk run -entry main fixtures/gen/undef_main.mkobj 2>undef.err; then
  echo "ERROR: undefined-symbol case unexpectedly succeeded" >&2
  exit 1
else
  echo "    diagnosed as expected:"
  sed 's/^/      /' undef.err
fi
rm -f undef.err

echo "==> section map (GC decisions)"
go run ./cmd/mylnk link -map -entry main fixtures/gen/ws_main.mkobj fixtures/gen/ws_weak.mkobj fixtures/gen/ws_strong.mkobj

echo "==> ALL VERIFICATION STEPS PASSED ($RUN_ID)"
