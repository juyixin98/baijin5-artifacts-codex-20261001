#!/usr/bin/env bash
# Example calls against a locally running service (npm run build && npm start).
# Each call prints the status, the selected representation (or explicit
# failure category) and the Vary cache dimensions.
set -u

BASE="${BASE:-http://127.0.0.1:4080}"

call() {
  local title="$1"; shift
  printf '\n### %s\n' "$title"
  curl -s -D - -o /tmp/opp408-body "$@" \
    | grep -iE '^HTTP/|^vary:|^content-type:|^content-language:|^x-selected-representation:|^x-run-id:' \
    || true
  # Print either the failure category or the first body line.
  if head -c 1 /tmp/opp408-body | grep -q '{'; then
    python3 -c "import json,sys;d=json.load(open('/tmp/opp408-body'));print('category:',d.get('error',{}).get('category','(success)'))" 2>/dev/null || true
  else
    head -c 120 /tmp/opp408-body; echo
  fi
}

call "1. no headers (RFC neutral default; still sends full Vary)" \
  "$BASE/welcome"

call "2. media weights + language prefix (zh -> zh-cn)" \
  -H 'Accept: text/html;q=0.4, application/json;q=0.9' \
  -H 'Accept-Language: zh' "$BASE/welcome"

call "3. specificity over q: exact json q=0 forbids json, */* keeps others" \
  -H 'Accept: application/json;q=0, */*;q=0.9' -H 'Accept-Language: *' "$BASE/welcome"

call "4. all media types forbidden -> 406 MEDIA_FORBIDDEN" \
  -H 'Accept: text/*;q=0, application/*;q=0' -H 'Accept-Language: *' "$BASE/welcome"

call "5. Accept-param matching (charset)" \
  -H 'Accept: text/plain;charset=utf-8' -H 'Accept-Language: en' "$BASE/welcome"

call "6. language fallback en-US -> en (penalty 0.9 per stripped subtag)" \
  -H 'Accept: application/json' -H 'Accept-Language: en-US;q=0.9' "$BASE/welcome"

call "7. independent dimensions: media ok, language missing -> 406" \
  -H 'Accept: application/json' -H 'Accept-Language: de' "$BASE/report"

call "8. invalid q dropped with a warning; valid item still served" \
  -H 'Accept: text/html;q=2, application/json' -H 'Accept-Language: en' "$BASE/welcome"

echo
echo "Inspect a run in detail:"
echo "  curl -s '$BASE/diagnostics' | python3 -m json.tool"
echo "  curl -s '$BASE/diagnostics?runId=<X-Run-Id>' | python3 -m json.tool"
