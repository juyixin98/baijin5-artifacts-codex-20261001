#!/usr/bin/env bash
# Local verification for the Modbus TCP master/slave fixture.
# Everything runs on loopback against the in-process fixture or the built
# binary — NO real industrial device is ever contacted.
set -euo pipefail

cd "$(dirname "$0")"

# The environment exports GOFLAGS=-mod=mod by default, which conflicts with
# workspace mode; -mod=readonly is always valid under go.work.
export GOFLAGS=-mod=readonly
export CGO_ENABLED=1   # required by github.com/mattn/go-sqlite3

MODS=(vectors mbap core config client fixture compat)

echo "=========================================================="
echo " 1/5  gofmt + go vet (all modules)"
echo "=========================================================="
unformatted="$(gofmt -l mbap vectors core config client fixture compat)"
if [[ -n "$unformatted" ]]; then
  echo "ERROR: unformatted files:"; echo "$unformatted"; exit 1
fi
echo "gofmt: clean"
for m in "${MODS[@]}"; do
  echo "-- vet $m"
  (cd "$m" && go vet ./...)
done

echo
echo "=========================================================="
echo " 2/5  unit + protocol tests WITH -race"
echo "=========================================================="
for m in vectors mbap core config client; do
  echo "-- test $m"
  (cd "$m" && go test -race -count=1 ./...)
done

echo
echo "=========================================================="
echo " 3/5  black-box compatibility tests (real loopback TCP)"
echo "      half/sticky packets, bad qty, address overflow,"
echo "      out-of-order identity, simultaneous read/write"
echo "=========================================================="
(cd compat && go test -race -count=1 -v ./...)

echo
echo "=========================================================="
echo " 4/5  coverage"
echo "=========================================================="
for m in mbap core config client; do
  # Scope coverage to the library package in each module ('.'), excluding
  # the thin, I/O-bound command mains under cmd/.
  (cd "$m" && go test -count=1 -coverprofile="/tmp/cov_$m.out" . >/dev/null)
  printf "  %-8s " "$m"
  (cd "$m" && go tool cover -func="/tmp/cov_$m.out" | tail -1)
done
(cd compat && go test -count=1 \
  -coverpkg=modbusfixture/core/...,modbusfixture/fixture,modbusfixture/mbap,modbusfixture/config \
  -coverprofile=/tmp/cov_bb.out ./... >/dev/null)
printf "  %-8s " "bb-all"
go tool cover -func=/tmp/cov_bb.out | tail -1

echo
echo "=========================================================="
echo " 5/5  real-binary smoke test"
echo "=========================================================="
mkdir -p run
go build -o run/modbus-fixture ./fixture/cmd/modbus-fixture
go build -o run/modbus-req      ./client/cmd/modbus-req

DB=run/verify.db
rm -f "$DB" "$DB-wal" "$DB-shm"
cat > run/verify.json <<JSON
{
  "listen": "127.0.0.1:5020",
  "control_listen": "127.0.0.1:15020",
  "sqlite_db": "$DB",
  "workers": 8,
  "latency": "none",
  "jitter_slot_ms": 2,
  "units": [{"unit_id": 1, "register_count": 125, "initial_values": [4660, 13124]}]
}
JSON

./run/modbus-fixture -config run/verify.json >run/verify-server.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1

echo "-- read  (expect 0x1234,0x3344)"
./run/modbus-req -addr 127.0.0.1:5020 -unit 1 read -start 0 -qty 2
echo "-- write (3 regs) then read back"
./run/modbus-req -addr 127.0.0.1:5020 -unit 1 write -start 10 -val 0x0001,0x0002,0xffff
./run/modbus-req -addr 127.0.0.1:5020 -unit 1 read  -start 10 -qty 3
echo "-- unsupported FC04 (expect exception 0x01)"
./run/modbus-req -addr 127.0.0.1:5020 -unit 1 raw -pdu 0400000001 || true
echo "-- address overflow (expect exception 0x02, non-zero exit)"
if ./run/modbus-req -addr 127.0.0.1:5020 -unit 1 read -start 999 -qty 1; then
  echo "ERROR: overflow read unexpectedly succeeded"; exit 1
else
  echo "ok: overflow rejected"
fi
echo "-- control plane"
curl -fsS http://127.0.0.1:15020/healthz >/dev/null && echo "healthz OK"
curl -fsS http://127.0.0.1:15020/stats   >/dev/null && echo "stats   OK"

echo
echo "ALL VERIFICATION STEPS COMPLETED"
