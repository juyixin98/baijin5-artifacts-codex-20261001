#!/usr/bin/env bash
# Example: scan synthetic sequences against the AC-dinucleotide motif.
# Prereq: server running (see README: uvicorn app.main:app --port 8000).
set -euo pipefail

HOST="${HOST:-http://127.0.0.1:8000}"

echo "== health =="
curl -s "$HOST/v1/health" | python3 -m json.tool

echo "== active configuration =="
curl -s "$HOST/v1/config" | python3 -m json.tool

echo "== scan =="
RESPONSE=$(curl -s -X POST "$HOST/v1/scan" \
  -H 'Content-Type: application/json' \
  -d @"$(dirname "$0")/scan_request.json")
echo "$RESPONSE" | python3 -m json.tool

echo "== provenance lookup =="
REQUEST_ID=$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
curl -s "$HOST/v1/scan/$REQUEST_ID" | python3 -m json.tool
