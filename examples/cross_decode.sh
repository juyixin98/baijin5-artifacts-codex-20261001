#!/usr/bin/env bash
# Minimal example: build an integrity-protected request with the independent
# Python oracle, send it to the Go server, then decode the Go response with the
# SAME independent oracle (a second opinion that shares no code with Go).
#
# Usage: ./examples/cross_decode.sh [server-host:port] [key]
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ADDR="${1:-127.0.0.1:34501}"
HOST="${ADDR%:*}"; PORT="${ADDR##*:}"
KEY="${2:-demo}"

# Start the Go server on an ephemeral demo port unless one is already provided.
STARTED=0
if [ "${1:-}" = "" ]; then
  CGO_ENABLED=1 go build -mod=vendor -o bin/stund ./cmd/stund
  ./bin/stund -net udp4 -addr "$ADDR" -key "$KEY" >/tmp/stund-example.out 2>&1 &
  SRV=$!
  STARTED=1
  sleep 0.6
fi

python3 - "$HOST" "$PORT" "$KEY" <<'PY'
import os, socket, sys, subprocess
sys.path.insert(0, "test/oracle")
import stun_oracle as o

host, port, key = sys.argv[1], int(sys.argv[2]), sys.argv[3].encode()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2)
txn = os.urandom(12)
req = o.add_message_integrity(o.METHOD_BINDING, o.CLASS_REQUEST, txn,
                              [(o.ATTR_SOFTWARE, b"cross-decode-example")], key)
s.sendto(req, (host, port))
data, _ = s.recvfrom(2048)
open("/tmp/stun-response.bin", "wb").write(data)

# Independent verification before decoding.
m = o.unmarshal(data)
o.verify_message_integrity(data, key)
assert m["txn"] == txn, "transaction id mismatch"
ip, rport = o.decode_xor_mapped_address(o.attr(m, o.ATTR_XOR_MAPPED_ADDRESS), txn)
print(f"independent oracle: HMAC valid, txn echoed, endpoint={ip}:{rport}")
PY

echo "--- full independent decode follows ---"
python3 test/oracle/stun_oracle.py decode /tmp/stun-response.bin

[ "$STARTED" = 1 ] && kill "$SRV" 2>/dev/null || true
