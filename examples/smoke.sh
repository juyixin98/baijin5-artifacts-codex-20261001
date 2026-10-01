#!/usr/bin/env bash
# End-to-end example calls against a locally running tribool-index server.
#
# Usage:
#   cargo run --bin server            # in one terminal
#   ./examples/smoke.sh               # in another
#
# Requires: curl, python3 (for pretty output). Override BASE/PORT as needed.

set -euo pipefail

BASE="${BASE:-http://127.0.0.1:${PORT:-8080}}"
CT='Content-Type: application/json'

post() { curl -s -H "$CT" "$BASE$1" -d "$2"; }

echo "== health =="
curl -s "$BASE/health"; echo

echo "== create table (4 rows, NULLs present) =="
post /tables '{
  "name": "people",
  "columns": [
    {"name": "city", "type": "text"},
    {"name": "age", "type": "int"},
    {"name": "active", "type": "bool"}
  ],
  "rows": [
    {"city":"Beijing","age":25,"active":true},
    {"city":"Beijing","age":null,"active":false},
    {"city":"Shanghai","age":40,"active":null},
    {"city":"Beijing","age":17,"active":true}
  ]
}'; echo

echo "== query: age >= 18 AND active = true =="
post /tables/people/query '{
  "filter": {"op":"and","args":[
    {"op":"cmp","column":"age","cmp":">=","value":18},
    {"op":"cmp","column":"active","cmp":"=","value":true}
  ]}
}' | python3 -m json.tool

echo "== query: NOT (age = 40) — NULL row stays UNKNOWN =="
post /tables/people/query '{
  "filter": {"op":"not","arg":{"op":"cmp","column":"age","cmp":"=","value":40}}
}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print("counts:", d["counts"], "matched:", d["matched_ids"])'

echo "== delete rows 2,3 (versioned delete, bumps version) =="
post /tables/people/delete '{"ids":[2,3]}'; echo

echo "== IS NULL after delete — deleted rows excluded, version reported =="
post /tables/people/query '{"filter":{"op":"cmp","column":"age","cmp":"is_null"}}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("version:", d["version"], "counts:", d["counts"], "matched:", d["matched_ids"])'

echo "== error path: int column vs text literal -> TYPE_MISMATCH, ok=false =="
post /tables/people/query '{"filter":{"op":"cmp","column":"age","cmp":"=","value":"x"}}'; echo
