#!/usr/bin/env bash
# Local demo: build imapd + imapdemo, seed the fixture mailbox, run a
# scripted session showing SELECT / FETCH / UID FETCH / STORE / EXPUNGE
# and the sequence-number shift caused by deletion.
set -euo pipefail
cd "$(dirname "$0")/.."

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"; kill "${SERVER_PID:-}" 2>/dev/null || true' EXIT

echo "== building =="
go build -o "$WORKDIR/imapd" ./cmd/imapd
go build -o "$WORKDIR/imapdemo" ./cmd/imapdemo

echo "== starting imapd on an ephemeral port (db in $WORKDIR) =="
# Port 0: the kernel picks a free port; the bound address is read back
# from the server log so the demo never collides with a running service.
"$WORKDIR/imapd" -addr 127.0.0.1:0 -db "$WORKDIR/demo.db" \
	-seed testdata/messages -uidvalidity 20260105 \
	>"$WORKDIR/server.log" 2>&1 &
SERVER_PID=$!

ADDR=""
for _ in $(seq 1 50); do
	ADDR="$(sed -n 's/.*listening on //p' "$WORKDIR/server.log" | head -1)"
	[ -n "$ADDR" ] && break
	sleep 0.1
done
if [ -z "$ADDR" ]; then
	echo "server did not start; log follows" >&2
	cat "$WORKDIR/server.log" >&2
	exit 1
fi
echo "== imapd listening on $ADDR =="

echo "== demo session (C: client, S: server) =="
"$WORKDIR/imapdemo" -addr "$ADDR"

echo "== server log =="
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
cat "$WORKDIR/server.log"
