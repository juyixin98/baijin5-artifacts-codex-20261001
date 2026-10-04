#!/usr/bin/env bash
# End-to-end demo against a locally running server.
# Usage: ./examples/demo.sh [base_url]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
ROUND="demo-$(date +%s)"
NOW=$(date +%s)
COMMIT_DEADLINE=$((NOW + 8))
REVEAL_DEADLINE=$((NOW + 60))

# Local synthetic fixture secrets (hex). random_value = 32 bytes, salt = 16 bytes.
ALICE_RV=$(printf 'aa%.0s' {1..32}); ALICE_SALT=$(printf 'a1%.0s' {1..16})
BOB_RV=$(printf 'bb%.0s' {1..32});   BOB_SALT=$(printf 'b2%.0s' {1..16})

commitment() { # round_id participant_id random_value salt
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import hashlib, sys
round_id, pid, rv, salt = sys.argv[1:5]
def enc(fields):
    out = b""
    for f in fields:
        out += len(f).to_bytes(4, "big") + f
    return out
print(hashlib.sha256(
    b"CRP1-COMMIT-v1" + enc([round_id.encode(), pid.encode(),
                             bytes.fromhex(rv), bytes.fromhex(salt)])
).hexdigest())
PY
}

ALICE_C=$(commitment "$ROUND" alice "$ALICE_RV" "$ALICE_SALT")
BOB_C=$(commitment "$ROUND" bob "$BOB_RV" "$BOB_SALT")

echo "== create round $ROUND (commit deadline in 8s) =="
curl -sS -X POST "$BASE/rounds" -H 'Content-Type: application/json' -d "{
  \"round_id\": \"$ROUND\",
  \"participants\": [\"alice\", \"bob\"],
  \"commit_deadline\": $COMMIT_DEADLINE,
  \"reveal_deadline\": $REVEAL_DEADLINE
}" | python3 -m json.tool

echo "== alice and bob commit =="
curl -sS -X POST "$BASE/rounds/$ROUND/commitments" -H 'Content-Type: application/json' \
  -d "{\"participant_id\": \"alice\", \"commitment\": \"$ALICE_C\"}"
echo
curl -sS -X POST "$BASE/rounds/$ROUND/commitments" -H 'Content-Type: application/json' \
  -d "{\"participant_id\": \"bob\", \"commitment\": \"$BOB_C\"}"
echo

echo "== a late commit is rejected once the deadline passes =="
sleep 9
curl -sS -X POST "$BASE/rounds/$ROUND/commitments" -H 'Content-Type: application/json' \
  -d "{\"participant_id\": \"alice\", \"commitment\": \"$ALICE_C\"}" | python3 -m json.tool

echo "== a wrong-salt reveal is rejected =="
curl -sS -X POST "$BASE/rounds/$ROUND/reveals" -H 'Content-Type: application/json' \
  -d "{\"participant_id\": \"alice\", \"random_value\": \"$ALICE_RV\", \"salt\": \"00112233445566778899aabbccddeeff\"}" | python3 -m json.tool

echo "== both reveal correctly =="
curl -sS -X POST "$BASE/rounds/$ROUND/reveals" -H 'Content-Type: application/json' \
  -d "{\"participant_id\": \"alice\", \"random_value\": \"$ALICE_RV\", \"salt\": \"$ALICE_SALT\"}"
echo
curl -sS -X POST "$BASE/rounds/$ROUND/reveals" -H 'Content-Type: application/json' \
  -d "{\"participant_id\": \"bob\", \"random_value\": \"$BOB_RV\", \"salt\": \"$BOB_SALT\"}"
echo

echo "== finalize and inspect public evidence =="
curl -sS -X POST "$BASE/rounds/$ROUND/finalize" | python3 -m json.tool

echo "== export evidence and verify it offline =="
curl -sS "$BASE/rounds/$ROUND/evidence" > /tmp/crp-evidence.json
python3 scripts/verify_evidence.py /tmp/crp-evidence.json

echo "== audit trail (request ids, outcomes, masked secrets) =="
curl -sS "$BASE/rounds/$ROUND/audit" | python3 -m json.tool
