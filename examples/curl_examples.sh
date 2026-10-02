#!/usr/bin/env bash
# Service call examples against a locally running instance.
# Start the service first:  python -m uvicorn app.main:app --port 8000
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "== health / version =="
curl -s "$BASE/v1/health"; echo
curl -s "$BASE/v1/version"; echo

echo "== list fixtures (ground truth included) =="
curl -s "$BASE/v1/fixtures"; echo

echo "== estimate: known sub-pixel shift fixture =="
curl -s -X POST "$BASE/v1/estimate" \
  -H 'content-type: application/json' \
  -H 'x-request-id: example-1' \
  -d '{
        "request_id": "example-1",
        "reference": {"fixture_id": "subpixel_shift", "role": "reference"},
        "moving":    {"fixture_id": "subpixel_shift", "role": "moving"}
      }'; echo

echo "== estimate: constant image (expected failure category flat_response) =="
curl -s -X POST "$BASE/v1/estimate" \
  -H 'content-type: application/json' \
  -d '{
        "reference": {"fixture_id": "constant_image", "role": "reference"},
        "moving":    {"fixture_id": "constant_image", "role": "moving"}
      }'; echo

echo "== estimate: periodic texture (expected uncertainty ambiguous_peaks) =="
curl -s -X POST "$BASE/v1/estimate" \
  -H 'content-type: application/json' \
  -d '{
        "reference": {"fixture_id": "periodic_texture", "role": "reference"},
        "moving":    {"fixture_id": "periodic_texture", "role": "moving"}
      }'; echo

echo "== validate all fixtures against ground truth =="
curl -s -X POST "$BASE/v1/validate" \
  -H 'content-type: application/json' \
  -d '{"tolerance_px": 0.5}'; echo

echo "== upload your own pair (base64 PNG) =="
REF_B64=$(python3 -c "import base64;print(base64.b64encode(open('fixtures/data/integer_shift_ref.png','rb').read()).decode())")
MOV_B64=$(python3 -c "import base64;print(base64.b64encode(open('fixtures/data/integer_shift_mov.png','rb').read()).decode())")
curl -s -X POST "$BASE/v1/estimate" \
  -H 'content-type: application/json' \
  -d "{\"reference\": {\"png_base64\": \"$REF_B64\"}, \"moving\": {\"png_base64\": \"$MOV_B64\"}}"; echo
