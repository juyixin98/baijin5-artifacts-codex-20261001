#!/usr/bin/env bash
# End-to-end smoke script against a locally running server.
# Usage: ./examples/curl_demo.sh [base_url]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8080}"
here="$(cd "$(dirname "$0")/.." && pwd)"

say() { printf '\n### %s\n' "$1"; }

say "health"
curl -s "$BASE/health" | python3 -m json.tool

say "triangle query (K5 -> 10 triangles, zero intermediates)"
curl -s "$BASE/query" \
  -H 'content-type: application/json' \
  -d @"$here/fixtures/requests/triangle.json" | python3 -m json.tool

say "skew sparse-intersection (5 answers; naive materialises 40,405 prefixes)"
curl -s "$BASE/query" \
  -H 'content-type: application/json' \
  -d @"$here/fixtures/requests/skew.json" | python3 -m json.tool

say "pagination: page through the 12-row result with limit=5"
python3 - "$BASE" "$here/fixtures/requests/page1.json" <<'PY'
import json, sys, urllib.request

base, fixture_path = sys.argv[1], sys.argv[2]
request = json.load(open(fixture_path))
total = 0
page = 0
while True:
    page += 1
    req = urllib.request.Request(
        base + "/query",
        data=json.dumps(request).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        body = json.load(resp)
    rows = body["rows"]
    total += len(rows)
    print(f"page {page}: {len(rows)} rows, stop={body['stats']['stop_reason']}")
    cursor = body.get("next_cursor")
    if not cursor:
        break
    request["cursor"] = cursor
print(f"paged total = {total} (expected 12, no loss/duplication)")
assert total == 12
PY

say "rejection: NULL in join key under default strict policy"
curl -s -i "$BASE/query" -H 'content-type: application/json' -d '{
  "relations": [
    {"name":"r","columns":[{"name":"a","type":"int"}],"rows":[[null]]},
    {"name":"s","columns":[{"name":"a","type":"int"}],"rows":[[1]]}
  ]
}' | sed -n '1,20p'
