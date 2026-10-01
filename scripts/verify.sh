#!/usr/bin/env bash
#
# verify.sh — reproducible verification for the local-whitelist SOCKS5 proxy.
#
# It runs four classes of checks and exits non-zero if any required check
# fails. Checks that cannot run in this environment (missing tools or an
# unusable loopback) are reported explicitly as SKIP, never as PASS.
#
#   1. Static:  gofmt, go vet
#   2. Unit:    go test -race, statement coverage >= 80% for ./internal/...
#   3. Build:   all binaries compile
#   4. E2E:     real socks5d + echod driven by the standalone smokeprobe:
#                 - IPv4 CONNECT success, byte-exact echo, directional EOF
#                 - domain CONNECT success (static resolver)
#                 - non-whitelisted IP is refused (REP 2, never dialed)
#                 - username/password: wrong creds rejected, right creds work
#                 - IPv6 loopback (skipped if ::1 is unavailable)
#
set -u
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; NC=$'\033[0m'
FAILURES=0; SKIPS=()

ok()   { printf '%sPASS%s %s\n' "$GREEN" "$NC" "$1"; }
bad()  { printf '%sFAIL%s %s\n' "$RED" "$NC" "$1"; FAILURES=$((FAILURES+1)); }
info() { printf '       %s\n' "$1"; }
skip() { printf '%sSKIP%s %s\n' "$YELLOW" "$NC" "$1"; SKIPS+=("$1"); }

TMPDIR_RUN="$(mktemp -d)"
trap 'cleanup' EXIT
cleanup() {
  if [[ -n "${ECHO_PID:-}" ]]; then kill "$ECHO_PID" 2>/dev/null || true; fi
  if [[ -n "${ECHO6_PID:-}" ]]; then kill "$ECHO6_PID" 2>/dev/null || true; fi
  if [[ -n "${PROXY_PID:-}" ]]; then kill "$PROXY_PID" 2>/dev/null || true; fi
  if [[ -n "${AUTH_PROXY_PID:-}" ]]; then kill "$AUTH_PROXY_PID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  rm -rf "$TMPDIR_RUN"
}

# ---------------------------------------------------------------------------
# 1. Static checks
# ---------------------------------------------------------------------------
echo "== 1. Static checks =="
UNFORMATTED="$(gofmt -l . 2>/dev/null)"
if [[ -z "$UNFORMATTED" ]]; then ok "gofmt: all files formatted"; else
  bad "gofmt: unformatted files:"; printf '%s\n' "$UNFORMATTED" | sed 's/^/        /'
fi

if go vet ./...; then ok "go vet"; else bad "go vet reported issues"; fi

# Optional external analyzers: report, do not fail, when they are absent.
if command -v staticcheck >/dev/null 2>&1; then
  if staticcheck ./...; then ok "staticcheck"; else bad "staticcheck findings"; fi
else
  skip "staticcheck not installed (run: go install honnef.co/go/tools/cmd/staticcheck@latest)"
fi
if command -v gosec >/dev/null 2>&1; then
  if gosec -quiet ./...; then ok "gosec"; else bad "gosec findings"; fi
else
  skip "gosec not installed (run: go install github.com/securego/gosec/v2/cmd/gosec@latest)"
fi

# ---------------------------------------------------------------------------
# 2. Unit + integration tests with race detector and coverage gate
# ---------------------------------------------------------------------------
echo "== 2. Tests (race + coverage) =="
COV_PROFILE="$TMPDIR_RUN/cov.out"
if go test -race -timeout 120s -coverpkg=./internal/... -coverprofile="$COV_PROFILE" ./...; then
  ok "go test -race ./..."
else
  bad "go test ./..."
fi

TOTAL_COV="$(go tool cover -func="$COV_PROFILE" 2>/dev/null | awk '/^total:/{gsub("%","",$NF); print $NF}')"
if [[ -n "$TOTAL_COV" ]] && awk "BEGIN{exit !($TOTAL_COV >= 80.0)}"; then
  ok "coverage ${TOTAL_COV}% (>= 80%)"
else
  bad "coverage ${TOTAL_COV:-unknown}% below 80%"
fi

# ---------------------------------------------------------------------------
# 3. Build all binaries
# ---------------------------------------------------------------------------
echo "== 3. Build =="
BIN="$TMPDIR_RUN/bin"; mkdir -p "$BIN"
if go build -o "$BIN/socks5d"  ./cmd/socks5d \
  && go build -o "$BIN/echod"    ./cmd/echod \
  && go build -o "$BIN/smokeprobe" ./cmd/smokeprobe; then
  ok "built socks5d, echod, smokeprobe"
else
  bad "build failed"; exit 1
fi

# ---------------------------------------------------------------------------
# 4. End-to-end against the real binaries
# ---------------------------------------------------------------------------
echo "== 4. End-to-end (real binaries) =="

# 4.1 local echo target (offline)
"$BIN/echod" -addr 127.0.0.1:0 >"$TMPDIR_RUN/echod.log" 2>&1 &
ECHO_PID=$!
ECHO_ADDR=""
for _ in $(seq 1 50); do
  ECHO_ADDR="$(sed -n 's/.*listening on //p' "$TMPDIR_RUN/echod.log" | head -1)"
  [[ -n "$ECHO_ADDR" ]] && break
  sleep 0.05
done
if [[ -z "$ECHO_ADDR" ]]; then bad "echod did not start"; exit 1; fi
ECHO_PORT="${ECHO_ADDR##*:}"
info "echod at $ECHO_ADDR"

write_config() {
  local path="$1" listen="$2" db="$3"
  cat >"$path" <<JSON
{
  "listen": "$listen",
  "handshake_timeout": "5s",
  "dial_timeout": "3s",
  "idle_timeout": "0s",
  "shutdown_timeout": "2s",
  "max_connections": 16,
  "byte_budget_per_connection": 1048576,
  "database": {"path": "$db"},
  "resolver": {"hosts": {"echo.local": ["127.0.0.1"], "svc.internal": ["127.0.0.1"]}},
  "rules": [
    {"kind": "cidr", "value": "127.0.0.0/8"},
    {"kind": "cidr", "value": "::1/128"},
    {"kind": "domain", "value": "echo.local", "mode": "exact"},
    {"kind": "domain", "value": "internal", "mode": "suffix"}
  ]
}
JSON
}

# No-auth proxy on an ephemeral port.
write_config "$TMPDIR_RUN/noauth.json" "127.0.0.1:0" "$TMPDIR_RUN/noauth.db"
"$BIN/socks5d" -config "$TMPDIR_RUN/noauth.json" >"$TMPDIR_RUN/noauth.log" 2>&1 &
PROXY_PID=$!
NOAUTH_ADDR=""
for _ in $(seq 1 50); do
  NOAUTH_ADDR="$(sed -n 's/.*"addr":"\([^"]*\)".*/\1/p' "$TMPDIR_RUN/noauth.log" | head -1)"
  [[ -n "$NOAUTH_ADDR" ]] && break
  sleep 0.05
done
if [[ -z "$NOAUTH_ADDR" ]]; then bad "socks5d (no-auth) did not start"; cat "$TMPDIR_RUN/noauth.log"; exit 1; fi
info "proxy(no-auth) at $NOAUTH_ADDR"

# Auth-required proxy on a second ephemeral port.
write_config "$TMPDIR_RUN/auth.json" "127.0.0.1:0" "$TMPDIR_RUN/auth.db"
SOCKS5D_USERNAME="alice" SOCKS5D_PASSWORD="secret" \
  "$BIN/socks5d" -config "$TMPDIR_RUN/auth.json" >"$TMPDIR_RUN/auth.log" 2>&1 &
AUTH_PROXY_PID=$!
AUTH_ADDR=""
for _ in $(seq 1 50); do
  AUTH_ADDR="$(sed -n 's/.*"addr":"\([^"]*\)".*/\1/p' "$TMPDIR_RUN/auth.log" | head -1)"
  [[ -n "$AUTH_ADDR" ]] && break
  sleep 0.05
done
if [[ -z "$AUTH_ADDR" ]]; then bad "socks5d (auth) did not start"; cat "$TMPDIR_RUN/auth.log"; exit 1; fi
info "proxy(auth) at $AUTH_ADDR"

PAYLOAD="verify-$(date +%s)-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

probe() { # args passed straight to smokeprobe; returns its exit code
  "$BIN/smokeprobe" "$@"
}

# 4.2 IPv4 success: echo + half-close EOF, expect REP 0
if probe -proxy "$NOAUTH_ADDR" -type ipv4 -host 127.0.0.1 -port "$ECHO_PORT" \
     -payload "$PAYLOAD" -expect-rep 0; then
  ok "e2e IPv4 CONNECT REP=0, byte-exact echo, clean directional EOF"
else bad "e2e IPv4 success path"; fi

# 4.3 domain success through the static resolver
if probe -proxy "$NOAUTH_ADDR" -type domain -host echo.local -port "$ECHO_PORT" \
     -payload "$PAYLOAD" -expect-rep 0; then
  ok "e2e domain CONNECT REP=0 via static resolution"
else bad "e2e domain success path"; fi

# 4.4 non-whitelisted IP must be refused with REP 2 and never connected.
#     8.8.8.8 is rejected by policy BEFORE any dial, so this stays offline.
if probe -proxy "$NOAUTH_ADDR" -type ipv4 -host 8.8.8.8 -port 53 -expect-rep 2; then
  ok "e2e non-whitelisted IP refused (REP=2), no upstream dial"
else bad "e2e whitelist enforcement"; fi

# 4.5 non-whitelisted domain -> REP 2
if probe -proxy "$NOAUTH_ADDR" -type domain -host evil.example -port 80 -expect-rep 2; then
  ok "e2e non-whitelisted domain refused (REP=2)"
else bad "e2e domain whitelist enforcement"; fi

# 4.6 auth: wrong password rejected (probe expects -2 == auth failure)
if probe -proxy "$AUTH_ADDR" -method userpass -user alice -pass wrong \
     -type ipv4 -host 127.0.0.1 -port "$ECHO_PORT" -expect-rep -2; then
  ok "e2e wrong credentials rejected"
else bad "e2e wrong-credential rejection"; fi

# 4.7 auth: correct password then successful CONNECT
if probe -proxy "$AUTH_ADDR" -method userpass -user alice -pass secret \
     -type ipv4 -host 127.0.0.1 -port "$ECHO_PORT" -payload "$PAYLOAD" -expect-rep 0; then
  ok "e2e valid credentials -> CONNECT REP=0"
else bad "e2e valid-credential path"; fi

# 4.8 auth-required but client offers no-auth: expect negotiation failure (-1)
if probe -proxy "$AUTH_ADDR" -method none \
     -type ipv4 -host 127.0.0.1 -port "$ECHO_PORT" -expect-rep -1; then
  ok "e2e method negotiation failure -> 0xFF and close"
else bad "e2e no-acceptable-method path"; fi

# 4.9 IPv6 loopback, only when the platform provides a usable ::1.
"$BIN/echod" -addr '[::1]:0' >"$TMPDIR_RUN/echod6.log" 2>&1 &
ECHO6_PID=$!
ECHO6_ADDR=""
for _ in $(seq 1 20); do
  ECHO6_ADDR="$(sed -n 's/.*listening on //p' "$TMPDIR_RUN/echod6.log" | head -1)"
  [[ -n "$ECHO6_ADDR" ]] && break
  sleep 0.05
  if ! kill -0 "$ECHO6_PID" 2>/dev/null; then break; fi
done
if [[ -n "$ECHO6_ADDR" ]]; then
  # Address looks like [::1]:<port>; strip brackets and host.
  ECHO6_PORT="${ECHO6_ADDR##*:}"
  if probe -proxy "$NOAUTH_ADDR" -type ipv6 -host "::1" -port "$ECHO6_PORT" \
       -payload "$PAYLOAD" -expect-rep 0; then
    ok "e2e IPv6 loopback CONNECT REP=0"
  else bad "e2e IPv6 loopback path"; fi
else
  skip "IPv6 loopback unavailable on this host"
fi
kill "$ECHO6_PID" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "================ SUMMARY ================"
if (( FAILURES == 0 )); then
  printf '%sALL REQUIRED CHECKS PASSED%s\n' "$GREEN" "$NC"
else
  printf '%s%d CHECK(S) FAILED%s\n' "$RED" "$FAILURES" "$NC"
fi
if (( ${#SKIPS[@]} > 0 )); then
  echo "Skipped (NOT counted as passed):"
  for s in "${SKIPS[@]}"; do printf '  - %s\n' "$s"; done
  if [[ -z "$ECHO6_ADDR" ]]; then echo "  - IPv6 loopback e2e (no usable ::1)"; fi
fi
exit "$FAILURES"
