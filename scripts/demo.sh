#!/usr/bin/env bash
# Local demo for the IMAPlite constrained query service.
#
# It builds and starts the server, then walks through the headline scenarios
# using raw TCP (via a tiny python client):
#   1. SELECT and FETCH declared data items
#   2. sequence numbers shift under deletion while UIDs stay attached
#   3. a stale UIDVALIDITY invalidates cached UIDs
#   4. a byte-exact binary literal
#   5. interleaved/pipelined tagged commands
#
# All data is synthetic and local. No production account or real mailbox is
# ever contacted.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDR="127.0.0.1:1143"
CTL="127.0.0.1:1144"
DB="${TMPDIR:-/tmp}/imaplite-demo-$$.db"
BIN="$ROOT/imaplite-demo-bin"

cleanup() {
  if [[ -n "${SRV_PID:-}" ]] && kill -0 "$SRV_PID" 2>/dev/null; then
    kill "$SRV_PID" 2>/dev/null || true
    wait "$SRV_PID" 2>/dev/null || true
  fi
  rm -f "$BIN" "$DB" "$DB-wal" "$DB-shm"
}
trap cleanup EXIT

echo "==> building"
( cd "$ROOT" && go build -o "$BIN" ./cmd/imaplite )

echo "==> starting server on $ADDR (db=$DB)"
"$BIN" -addr "$ADDR" -ctl-addr "$CTL" -db "$DB" -data "$ROOT/data" &
SRV_PID=$!
sleep 1

python3 - "$ADDR" "$CTL" <<'PY'
import socket, sys, time

addr, ctl = sys.argv[1], sys.argv[2]

def endpoint(a):
    host, port = a.rsplit(":", 1)
    return (host, int(port))

class Imap:
    def __init__(self):
        self.s = socket.create_connection(endpoint(addr), timeout=5)
        self.f = self.s.makefile("rb")
        self.tag = 0
        print("    " + self.readline().decode().rstrip())

    def readline(self):
        line = self.f.readline()
        # transparently consume a non-synchronizing literal if present
        if b"{" in line and line.rstrip(b"\r\n").endswith(b"}"):
            i = line.rindex(b"{")
            n = int(line[i+1:line.rindex(b"}")])
            self.f.read(n)
        return line

    def cmd(self, text, literal=None):
        self.tag += 1
        tag = f"a{self.tag}"
        if literal is not None and "{n}" in text:
            head, tail = text.split("{n}", 1)
            self.s.sendall(f"{tag} {head}{{{len(literal)}}}\r\n".encode())
            print("    " + self.readline().decode().rstrip(), "(continuation)")
            self.s.sendall(literal)
            self.s.sendall((tail + "\r\n").encode())
        else:
            self.s.sendall(f"{tag} {text}\r\n".encode())
        print(f">>> {tag} {text}")
        while True:
            line = self.readline()
            print("    " + line.decode(errors="replace").rstrip())
            if line.startswith(tag.encode() + b" "):
                break

def ctl_cmd(text):
    c = socket.create_connection(endpoint(ctl), timeout=5)
    cf = c.makefile("rb")
    cf.readline()
    c.sendall((text + "\r\n").encode())
    print(f"[ctl] {text}")
    print("    " + cf.readline().decode().rstrip())
    c.close()

print("\n=== 1. SELECT + FETCH declared data items ===")
m = Imap()
m.cmd("LOGIN tester Tester#2025!")
m.cmd("SELECT INBOX")
m.cmd("FETCH 1:5 (UID FLAGS RFC822.SIZE)")
m.cmd("UID FETCH 3 (ENVELOPE BODYSTRUCTURE)")

print("\n=== 2. deletion shifts sequence numbers, UIDs keep identity ===")
m.cmd("FETCH 3 (UID)")
m.cmd("STORE 2 +FLAGS (\\Deleted)")
m.cmd("EXPUNGE")
m.cmd("FETCH 2 (UID BODY[HEADER.FIELDS (SUBJECT)])")  # old seq-3, same UID

print("\n=== 3. stale UIDVALIDITY invalidates old UIDs ===")
ctl_cmd("ROTATE INBOX")
m.cmd("UID FETCH 1 (UID)")                              # NO [UIDVALIDITY]
m.cmd("SELECT INBOX")                                   # new epoch, empty
m.s.close()

print("\n=== 4. byte-exact binary literal ===")
ctl_cmd("RESEED")
# A fresh connection that authenticates with a byte-counted, non-ASCII literal
# password, then selects and fetches the generated binary message.
bl = Imap()
bl.cmd("LOGIN lit {n}", literal="litpäss".encode())
bl.cmd("SELECT INBOX")
bl.cmd("UID FETCH 5 (UID RFC822.SIZE)")
bl.cmd("UID FETCH 5 (BODY[])")
bl.s.close()

print("\n=== 5. pipelined / interleaved commands keep tag association ===")
p = Imap()
p.cmd("LOGIN tester Tester#2025!")
p.cmd("SELECT INBOX")
p.s.sendall(b"p1 FETCH 1 (UID)\r\np2 FETCH 2 (UID)\r\np3 NOOP\r\n")
# Exactly three tagged completions (each FETCH also emits one FETCH record).
for _ in range(5):
    print("    " + p.readline().decode(errors="replace").rstrip())
p.s.close()

print("\nAll demo scenarios completed.")
PY

echo
echo "==> demo finished (server logs above; service still stops on exit)"
