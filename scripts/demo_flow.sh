#!/usr/bin/env bash
# End-to-end demo against a locally running server (scripts/run_server.sh).
#
# Flow: create batch -> client-side encrypt 3 values -> submit with
# coefficients -> aggregate -> decrypt -> verify -> show audit trail.
# Expected plaintext result is computed by hand: 2*3 + 5*(-2) + (-1)*7 = -11.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE_URL:-http://127.0.0.1:8000}"
PY=.venv/bin/python

echo "== service meta (versions / supported operations) =="
curl -s "$BASE/meta" | $PY -m json.tool

echo "== create batch =="
BATCH_JSON=$(curl -s -X POST "$BASE/batches" -H 'Content-Type: application/json' \
  -d '{"label": "demo", "key_size": 2048,
       "max_plaintext_abs": 1000000, "max_coefficient_abs": 1000,
       "max_aggregate_abs": 1000000000}')
echo "$BATCH_JSON" | $PY -m json.tool
BATCH_ID=$(echo "$BATCH_JSON" | $PY -c 'import json,sys; print(json.load(sys.stdin)["batch"]["batch_id"])')
KEY_ID=$(echo "$BATCH_JSON" | $PY -c 'import json,sys; print(json.load(sys.stdin)["batch"]["key_id"])')
N=$(echo "$BATCH_JSON" | $PY -c 'import json,sys; print(json.load(sys.stdin)["batch"]["public_key_n"])')

echo "== client-side encryption (plaintext never leaves this step) =="
# values: 3, -2, 7 ; coefficients: 2, 5, -1  -> expected sum -11
VALUES=(3 -2 7)
COEFFS=(2 5 -1)
for i in 0 1 2; do
  CT=$($PY scripts/client_encrypt.py "$N" "${VALUES[$i]}" \
       | $PY -c 'import json,sys; print(json.load(sys.stdin)["ciphertext"])')
  echo "-- submit participant-$i value=${VALUES[$i]} coeff=${COEFFS[$i]}"
  curl -s -X POST "$BASE/batches/$BATCH_ID/contributions" \
    -H 'Content-Type: application/json' \
    -d "{\"participant_id\": \"participant-$i\", \"key_id\": \"$KEY_ID\",
         \"ciphertext\": \"$CT\", \"coefficient\": ${COEFFS[$i]},
         \"plaintext_fixture\": ${VALUES[$i]}}" | $PY -m json.tool
done

echo "== aggregate (homomorphic, server sees no plaintext) =="
curl -s -X POST "$BASE/batches/$BATCH_ID/aggregate" | $PY -m json.tool

echo "== decrypt result (expect -11) =="
curl -s -X POST "$BASE/batches/$BATCH_ID/decrypt" | $PY -m json.tool

echo "== independent verification (expect PASS) =="
curl -s -X POST "$BASE/batches/$BATCH_ID/verify" | $PY -m json.tool

echo "== audit trail =="
curl -s "$BASE/batches/$BATCH_ID/audit" | $PY -m json.tool
