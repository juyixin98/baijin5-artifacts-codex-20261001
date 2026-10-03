#!/usr/bin/env bash
# Regenerates the synthetic mail fixtures under testdata/messages deterministically.
# All bytes are explicit (CRLF line endings, one binary message) so that
# testdata/manifest.sha256 produced by sha256sum(1) is an independent reference.
set -euo pipefail
mkdir -p "$(dirname "$0")/messages"
cd "$(dirname "$0")/messages"

crlf() { printf '%s\r\n' "$1"; }

{
	crlf 'From: alice@example.test'
	crlf 'To: bob@example.test'
	crlf 'Subject: Hello Bob'
	crlf 'Date: Mon, 05 Jan 2026 10:00:00 +0000'
	crlf 'Message-ID: <m1@example.test>'
	crlf ''
	crlf 'Hello Bob,'
	crlf 'this is the first fixture message.'
} >01.eml

{
	crlf 'From: carol@example.test'
	crlf 'To: dave@example.test'
	crlf 'Subject: 季度报表已更新'
	crlf 'Date: Tue, 06 Jan 2026 11:30:00 +0000'
	crlf 'Message-ID: <m2@example.test>'
	crlf ''
	crlf 'Hi Dave,'
	crlf 'the quarterly report is attached in the shared folder.'
	crlf 'Regards, Carol'
} >02.eml

{
	crlf 'From: erin@example.test'
	crlf 'To: frank@example.test'
	crlf 'Subject: Build results'
	crlf 'Date: Wed, 07 Jan 2026 09:15:00 +0000'
	crlf 'Message-ID: <m3@example.test>'
	crlf ''
	crlf 'Build 1042 succeeded.'
	crlf 'Tests: 318 passed, 0 failed.'
	crlf 'Artifacts uploaded to the local bucket.'
} >03.eml

{
	crlf 'From: grace@example.test'
	crlf 'To: heidi@example.test'
	crlf 'Subject: Lunch?'
	crlf 'Date: Thu, 08 Jan 2026 12:00:00 +0000'
	crlf 'Message-ID: <m4@example.test>'
	crlf ''
	crlf 'Are you free for lunch tomorrow?'
} >04.eml

{
	crlf 'From: bin@example.test'
	crlf 'To: bob@example.test'
	crlf 'Subject: binary payload'
	crlf 'Date: Fri, 09 Jan 2026 08:00:00 +0000'
	crlf 'Message-ID: <m5@example.test>'
	crlf ''
	# Body: every byte value 0x00..0xFF (includes NUL, CR, LF), then CRLF + END.
	for i in $(seq 0 255); do
		printf "$(printf '\\%03o' "$i")"
	done
	printf '\r\nEND\r\n'
} >05.eml

# Initial flags applied at seed time (space-separated, per file).
cat >flags.txt <<'EOF'
01.eml \Seen
EOF

cd ..
sha256sum messages/*.eml >manifest.sha256
wc -c messages/*.eml
