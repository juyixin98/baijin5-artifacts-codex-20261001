#!/usr/bin/env bash
# End-to-end smoke test against a locally running smtpsink.
# It performs an SMTP transaction over /dev/tcp (no external mail tool), then
# prints the persisted rows. Nothing leaves this machine.
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-2525}"
DB="${DB:-var/smtpsink.db}"

echo ">> opening connection to ${HOST}:${PORT}"
exec 3<>"/dev/tcp/${HOST}/${PORT}"

reply() { head -n 1 <&3; }

echo "S: $(reply)"

send() { printf '%s\r\n' "$1" >&3; }

send "EHLO example-laptop";        echo "C: EHLO";        reply
send "MAIL FROM:<sender@localhost>"; echo "C: MAIL";       reply
send "RCPT TO:<bob@sink.local>";    echo "C: RCPT (local)"; reply
send "RCPT TO:<bob@sink.local>";    echo "C: RCPT (duplicate)"; reply
send "RCPT TO:<evil@external.example>"; echo "C: RCPT (remote, expect 550)"; reply
send "DATA";                        echo "C: DATA";        reply
printf 'Subject: local test\r\n\r\nThis body has a dot line:\r\n.starts-with-dot\r\n.\r\n' >&3
echo "C: <body + terminator>";      reply
send "QUIT";                        echo "C: QUIT";        reply
exec 3<&-; exec 3>&-

echo
echo ">> persisted messages:"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 -header -column "$DB" "SELECT id, sender, body_bytes FROM messages;"
  echo
  echo ">> recipient copies (duplicate collapsed, remote absent):"
  sqlite3 -header -column "$DB" "SELECT message_id, mailbox FROM recipient_copies ORDER BY mailbox;"
else
  echo "sqlite3 CLI not installed; inspect $DB with any SQLite tool."
fi
