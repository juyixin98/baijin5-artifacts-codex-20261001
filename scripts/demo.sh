#!/usr/bin/env bash
# Local end-to-end demo: builds the workspace, starts psi-server, runs both
# parties against the fixtures, and diffs the PSI output against an
# independent plaintext reference computed with sort/comm (not the Rust core).
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PSI_DEMO_PORT:-39711}"
BASE="http://127.0.0.1:${PORT}"
DB_PATH="$(mktemp -u /tmp/psi-demo-XXXXXX.db)"
OUT_DIR="$(mktemp -d /tmp/psi-demo-out-XXXXXX)"
SERVER_PID=""

cleanup() {
    [[ -n "${SERVER_PID}" ]] && kill "${SERVER_PID}" 2>/dev/null || true
    rm -f "${DB_PATH}"
    rm -rf "${OUT_DIR}"
}
trap cleanup EXIT

echo "==> building (cargo build)"
cargo build --quiet

echo "==> starting psi-server on ${BASE} (db: ${DB_PATH})"
PSI_BIND_ADDR="127.0.0.1:${PORT}" PSI_DB_PATH="${DB_PATH}" \
    ./target/debug/psi-server >"${OUT_DIR}/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 50); do
    if curl -sf -o /dev/null -X POST "${BASE}/v1/sessions"; then
        break
    fi
    sleep 0.2
done

SESSION="$(./target/debug/psi-client --server "${BASE}" new-session)"
echo "==> session: ${SESSION}"

echo "==> starting party B (fixtures/bob_elements.txt)"
./target/debug/psi-client --server "${BASE}" run \
    --role b --session "${SESSION}" --file fixtures/bob_elements.txt \
    >"${OUT_DIR}/b.log" 2>&1 &
B_PID=$!

echo "==> running party A (fixtures/alice_elements.txt)"
./target/debug/psi-client --server "${BASE}" run \
    --role a --session "${SESSION}" --file fixtures/alice_elements.txt \
    >"${OUT_DIR}/intersection.txt" 2>"${OUT_DIR}/a.log"
wait "${B_PID}"

echo "==> computing independent plaintext reference with sort/comm"
comm -12 \
    <(sort -u fixtures/alice_elements.txt) \
    <(sort -u fixtures/bob_elements.txt) \
    > "${OUT_DIR}/expected.txt"

echo "==> comparing PSI output with plaintext reference"
if diff -u "${OUT_DIR}/expected.txt" <(sort -u "${OUT_DIR}/intersection.txt"); then
    echo "DEMO OK: PSI intersection matches plaintext intersection"
else
    echo "DEMO FAILED: mismatch" >&2
    exit 1
fi

echo "==> also matches frozen fixture fixtures/expected_intersection.txt"
diff -u fixtures/expected_intersection.txt <(sort -u "${OUT_DIR}/intersection.txt")

echo "==> audit log (redacted: counts + payload digests only)"
./target/debug/psi-client --server "${BASE}" audit --session "${SESSION}"

echo "==> transcript privacy check: no raw element may appear in server-visible data"
LEAK=0
HAVE_SQLITE3=0
command -v sqlite3 >/dev/null 2>&1 && HAVE_SQLITE3=1
while IFS= read -r element; do
    [[ -z "${element}" ]] && continue
    if grep -qF "${element}" "${OUT_DIR}/server.log"; then
        echo "LEAK: '${element}' found in server log" >&2
        LEAK=1
    fi
    if [[ "${HAVE_SQLITE3}" -eq 1 ]] \
        && sqlite3 "${DB_PATH}" "SELECT 1 FROM audit WHERE instr(detail, '${element}') > 0 LIMIT 1;" | grep -q 1; then
        echo "LEAK: '${element}' found in audit log" >&2
        LEAK=1
    fi
done < <(cat fixtures/alice_elements.txt fixtures/bob_elements.txt)
[[ "${HAVE_SQLITE3}" -eq 0 ]] && echo "(sqlite3 CLI not found; audit-log scan covered by cargo tests instead)"
[[ "${LEAK}" -eq 0 ]] && echo "transcript privacy OK"

echo "==> demo finished successfully"
