#!/usr/bin/env bash
# End-to-end local demo: synthetic prime-length fixture -> fft -> ifft ->
# verify, plus an embedded benchmark. No network, no external data.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BIN="$ROOT/build/fft_service"
DATA="$ROOT/data"
mkdir -p "$DATA"
[ -x "$BIN" ] || "$HERE/build.sh" Release >/dev/null

echo "================ 1. prime-length demo (N=13, Bluestein) ================"
"$BIN" demo --n 13 --request-id demo-prime-13

echo
echo "================ 2. local synthetic pipeline (N=97 prime) =============="
RID="demo-$(date +%s)"
"$BIN" generate --kind large-dynamic --n 97 --param 5 --seed 42 \
      --out "$DATA/in.cft" --request-id "$RID-gen" 2>/dev/null \
  | grep -E 'status|detail'
"$BIN" fft  --in "$DATA/in.cft" --out "$DATA/spec.cft" \
      --request-id "$RID-fft" 2>/dev/null | grep -E 'status|kernel|conv|roundTrip'
"$BIN" ifft --in "$DATA/spec.cft" --out "$DATA/back.cft" \
      --request-id "$RID-ifft" 2>/dev/null | grep -E 'status|roundTrip'
"$BIN" verify --in "$DATA/in.cft" --request-id "$RID-verify" 2>/dev/null \
  | grep -E 'status|maxAbs|failureReason'

echo
echo "================ 3. independent benchmark ============================="
"$BIN" bench --ref-max-n 512 2>/dev/null
