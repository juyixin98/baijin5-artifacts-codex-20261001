#!/usr/bin/env bash
# Local demo: builds the server and CLI, starts the service on a local
# port, and exercises every endpoint with the fixture programs.
set -euo pipefail
cd "$(dirname "$0")/.."

export GOFLAGS=-mod=readonly
PORT="${PORT:-18080}"
ADDR="127.0.0.1:${PORT}"
TMP="$(mktemp -d)"
SRV=""
cleanup() {
  rm -rf "$TMP"
  if [ -n "$SRV" ]; then kill "$SRV" 2>/dev/null || true; fi
}
trap cleanup EXIT

echo "== building server and cli =="
(cd service && go build -o "$TMP/pmd-server" ./cmd/pmd-server)
(cd service && go build -o "$TMP/pmd" ./cmd/pmd)

echo "== starting server on $ADDR =="
"$TMP/pmd-server" -config config/server.json -addr "$ADDR" >"$TMP/server.log" 2>&1 &
SRV=$!
for _ in $(seq 1 50); do
  if curl -sf "http://$ADDR/v1/health" >/dev/null 2>&1; then break; fi
  sleep 0.1
done

echo
echo "== GET /v1/health =="
curl -s "http://$ADDR/v1/health"

echo
echo "== POST /v1/compile (fixtures/programs/list.pmd, truncated) =="
"$TMP/pmd" req -p fixtures/programs/list.pmd -n 0 >"$TMP/compile.json"
curl -s -X POST "http://$ADDR/v1/compile" -H 'Content-Type: application/json' \
  --data @"$TMP/compile.json" | head -c 1200
echo "..."

echo
echo "== POST /v1/match (Cons(2, Cons(3, Nil)): guard fails, falls through) =="
"$TMP/pmd" req -p fixtures/programs/list.pmd \
  -v '{"ctor":"Cons","args":[{"lit":2},{"ctor":"Cons","args":[{"lit":3},{"ctor":"Nil"}]}]}' \
  -n 0 >"$TMP/match.json"
curl -s -X POST "http://$ADDR/v1/match" -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-match-1' --data @"$TMP/match.json"

echo
echo "== POST /v1/match (invalid value: bad arity) =="
"$TMP/pmd" req -p fixtures/programs/list.pmd \
  -v '{"ctor":"Cons","args":[{"lit":1}]}' -n 0 >"$TMP/match-bad.json"
curl -s -X POST "http://$ADDR/v1/match" -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-match-2' --data @"$TMP/match-bad.json"

echo
echo "== POST /v1/match (semantic error: unbound guard variable) =="
"$TMP/pmd" req -p fixtures/programs/bad_unbound.pmd \
  -v '{"ctor":"A","args":[{"lit":1}]}' -n 0 >"$TMP/match-err.json"
curl -s -X POST "http://$ADDR/v1/match" -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-match-3' --data @"$TMP/match-err.json"

echo
echo "== POST /v1/diff (guards.pmd: tree vs sequential interpreter) =="
"$TMP/pmd" req -p fixtures/programs/guards.pmd -n 200 >"$TMP/diff.json"
curl -s -X POST "http://$ADDR/v1/diff" -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-diff-1' --data @"$TMP/diff.json"

echo
echo "== CLI: pmd diff (exit code reflects mismatches) =="
"$TMP/pmd" diff -p fixtures/programs/tree.pmd -n 200 | head -c 400
echo "..."

echo
echo "== server log (structured, request-scoped) =="
head -n 12 "$TMP/server.log"

echo
echo "DEMO OK"
