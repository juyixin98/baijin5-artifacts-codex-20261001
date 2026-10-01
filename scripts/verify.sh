#!/usr/bin/env bash
#
# verify.sh — reproducible verification for the local-whitelist SOCKS5 proxy.
#
# It runs, in order:
#   1. gofmt check
#   2. go vet
#   3. full unit + integration test suite
#   4. race-detector suite
#   5. coverage (fails the build below the configured threshold)
#   6. production binary build
#   7. CLI smoke test (adduser + serve + a real proxied request)
#
# Only local loopback sockets and an on-disk SQLite file in a temp dir are
# used. Nothing connects to a non-local host.
set -euo pipefail

cd "$(dirname "$0")/.."

COVERAGE_MIN="${COVERAGE_MIN:-80.0}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

banner() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

banner "1/7 gofmt"
unformatted="$(gofmt -l cmd internal test)"
if [[ -n "$unformatted" ]]; then
  echo "gofmt needed on:"; echo "$unformatted"; exit 1
fi
echo "ok"

banner "2/7 go vet"
go vet ./...
echo "ok"

banner "3/7 tests (unit + integration)"
go test -count=1 -timeout 120s ./...
echo "ok"

banner "4/7 race detector"
go test -race -count=1 -timeout 180s ./internal/... ./test/...
echo "ok"

banner "5/7 coverage (threshold ${COVERAGE_MIN}%%)"
go test -count=1 -timeout 180s -coverprofile="$WORKDIR/cover.out" \
  -coverpkg=./internal/... ./internal/... ./test/... >/dev/null
go tool cover -func="$WORKDIR/cover.out" | tee "$WORKDIR/cover.txt"
total="$(grep '^total:' "$WORKDIR/cover.txt" | awk '{print $3}' | tr -d '%')"
echo "total coverage: ${total}%"
awk -v t="$total" -v m="$COVERAGE_MIN" 'BEGIN{ if (t+0 < m+0) exit 1 }' || {
  echo "coverage ${total}% below threshold ${COVERAGE_MIN}%"; exit 1; }

banner "6/7 build"
go build -o "$WORKDIR/socksproxy" ./cmd/socksproxy
echo "built $WORKDIR/socksproxy"

banner "7/7 CLI smoke test"
CFG="$WORKDIR/proxy.yaml"
DB="$WORKDIR/proxy.db"
cat >"$CFG" <<YAML
listen: "127.0.0.1:11080"
database: "$DB"
auth:
  required: true
limits:
  max_concurrent_connections: 16
  handshake_timeout: 5s
  resolve_timeout: 2s
  dial_timeout: 2s
  idle_timeout: 10s
  max_bytes_up: 1048576
  max_bytes_down: 1048576
log:
  level: "info"
  text_format: true
rules:
  - kind: cidr
    host: "127.0.0.0/8"
    ports: [0]
  - kind: cidr
    host: "::1/128"
    ports: [0]
YAML

SOCKS_PROXY_PASSWORD='smoke-pw' "$WORKDIR/socksproxy" adduser --config "$CFG" --name smoke

# Start an echo target and the proxy.
python3 - "$WORKDIR/target.port" <<'PY' &
import socket,sys,threading
s=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(5)
open(sys.argv[1],"w").write(str(s.getsockname()[1]))
def serve(c):
    while True:
        d=c.recv(4096)
        if not d: break
        c.sendall(d)
    c.close()
while True:
    c,_=s.accept(); threading.Thread(target=serve,args=(c,),daemon=True).start()
PY
TARGET_PID=$!
sleep 0.3
TARGET_PORT="$(cat "$WORKDIR/target.port")"

"$WORKDIR/socksproxy" serve --config "$CFG" >"$WORKDIR/proxy.log" 2>&1 &
PROXY_PID=$!
sleep 0.8
cleanup() { kill "$PROXY_PID" "$TARGET_PID" 2>/dev/null || true; }
trap cleanup EXIT

python3 - "$TARGET_PORT" <<'PY'
import socket,sys,struct
port=int(sys.argv[1])
def write_all(c,b): c.sendall(b)
c=socket.create_connection(("127.0.0.1",11080),timeout=5)
write_all(c,bytes([5,1,2]))
assert c.recv(2)==bytes([5,2]), "method selection"
u,p=b"smoke",b"smoke-pw"
write_all(c,bytes([1,len(u)])+u+bytes([len(p)])+p)
assert c.recv(2)==bytes([1,0]), "auth status"
req=bytes([5,1,0,1,127,0,0,1])+struct.pack("!H",port)
write_all(c,req)
rep=c.recv(10)
assert rep[1]==0, f"connect reply REP={rep[1]}"
msg=b"smoke-bytes-12345"
write_all(c,msg); c.shutdown(socket.SHUT_WR)
got=b""
while True:
    d=c.recv(64)
    if not d: break
    got+=d
assert got==msg, f"echo mismatch {got!r}"
print("smoke ok:",msg.decode())
PY

kill "$PROXY_PID" 2>/dev/null || true
sleep 0.2
grep -q "relay_completed" "$WORKDIR/proxy.log" && echo "request logged: relay_completed" || \
  { echo "expected relay_completed in proxy log"; cat "$WORKDIR/proxy.log"; exit 1; }

banner "ALL CHECKS PASSED"
