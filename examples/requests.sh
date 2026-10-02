#!/usr/bin/env bash
# End-to-end example against a locally running cow-snap server.
# Usage: ./examples/requests.sh [port]
set -euo pipefail
B=localhost:${1:-8080}

echo '--- create base snapshot'
curl -s -XPOST $B/snapshots -H 'content-type: application/json' -d '{}'; echo

echo '--- write pages 0,1 ("hello cow-snap!" base64)'
curl -s -XPOST $B/snapshots/1/writes -H 'content-type: application/json' -d \
  '{"writes":[{"page":0,"offset":0,"data_b64":"aGVsbG8gY293LXNuYXAh"},
              {"page":1,"offset":0,"data_b64":"aGVsbG8gY293LXNuYXAh"}]}'; echo

echo '--- fork snapshot 1 -> 2 (shares pages, copies nothing)'
curl -s -XPOST $B/snapshots -H 'content-type: application/json' -d '{"parent":1}'; echo

echo '--- child overwrites page 0 (exactly one COW copy)'
curl -s -XPOST $B/snapshots/2/writes -H 'content-type: application/json' -d \
  '{"writes":[{"page":0,"offset":0,"data_b64":"Q09XLWNvcGllZA=="}]}'; echo

echo '--- base page 0 unchanged'; curl -s $B/snapshots/1/pages/0; echo
echo '--- child page 0';        curl -s $B/snapshots/2/pages/0; echo

echo '--- diagnostics'
curl -s $B/diag/stats; echo
curl -s $B/diag/verify; echo

echo '--- input error (page 99)'
curl -s -XPOST $B/snapshots/2/writes -H 'content-type: application/json' -d \
  '{"writes":[{"page":99,"offset":0,"data_b64":"eA=="}]}'; echo

echo '--- delete base; child still readable'
curl -s -XDELETE $B/snapshots/1; echo
curl -s $B/snapshots/2/pages/1; echo
