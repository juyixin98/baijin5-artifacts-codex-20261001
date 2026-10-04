# Segmented AEAD Service (SSEA)

Authenticated encryption for **large, segmented messages**, built on mature
AEAD implementations. Segments carry a unique nonce bound to message identity,
sequence number and a termination marker; deletion, reordering, truncation
and nonce-reuse are all detectable. The full plaintext is published **only
after the entire stream authenticates**; per-segment fragments are staged in
permission-controlled storage with no partial-read API.

## Security properties

| Property | How it is enforced |
|---|---|
| Confidentiality + integrity per segment | AES-256-GCM via `cryptography` (OpenSSL); independently re-verifiable via PyCryptodome |
| Unique nonce per slot | 96-bit nonce = HMAC-SHA256(nonce_key, version‖message_id‖seqno‖final_flag); a final and non-final frame at the same index get different nonces |
| Delete / reorder detection | seqno and `total_segments` are inside the authenticated AAD; the receiver requires exactly slots `0..N-1` |
| Truncation / missing terminator | the IS_FINAL marker is authenticated and must sit only on slot `N-1`; release requires exactly one terminator |
| Length lying | declared stream length and per-segment length are authenticated; assembled length is checked against it |
| Retry safety | nonce is deterministic per slot; an identical retry is an idempotent replay, a different payload under the same nonce is rejected (`nonce_conflict`) |
| No premature plaintext | verify-then-stage; fragments are 0600 inside a 0700 dir with no read API; any failure shreds them; release is a fsync + atomic rename |
| Crash recovery | SQLite (WAL, `synchronous=FULL`) + staged files survive restart; interrupted streams are flagged *undecidable* and resumable by retransmission |
| Diagnostics | every accept/reject/inconclusive is an audit row + log line with request id and safe state; keys/plaintext/ciphertext are redacted |

## Module layout (real responsibilities, not a single script)

```
app/
  core/protocol.py    wire framing, nonce derivation, canonical AAD
  core/crypto.py      mature AEAD adapters (cryptography / PyCryptodome)
  core/keyring.py     local key material (0600 file or env), no keys in DB/logs
  core/sender.py      producer: split + seal + frame (never imported by receiver)
  core/verifier.py    independent offline decision oracle (no DB/service)
  core/staging.py     0700/0600 fragment staging, shred, atomic release
  core/service.py     the stream state machine / policy layer
  core/audit.py       structured diagnostics + redaction
  core/errors.py      stable failure categories
  db/store.py         SQLite streams / segment receipts / audit table
  api/schemas.py      request/response models
  api/deps.py         composition root
  api/server.py       thin FastAPI transport (blocking work in threadpool)
  config.py           env-driven settings
scripts/              fixture generator + HTTP calling example
fixtures/             committed out-of-band keys/vectors/KAT (test-only)
tests/unit            protocol, crypto, verifier, keyring, staging, audit
tests/integration     state machine scenarios + end-to-end HTTP
```

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt          # exact pins, see requirements.txt

# 1. generate local keys (0600) and fixtures
python -m app.core.keyring init ./fixtures/demo-keys.json
python scripts/generate_fixtures.py

# 2. run the service
SSEA_KEY_FILE=./fixtures/demo-keys.json \
SSEA_DB_PATH=./data/demo.sqlite3 \
python -m app                            # serves on 127.0.0.1:8080

# 3. in another shell, run the calling example (try --shuffle)
python scripts/client_example.py --shuffle
```

`fixtures/test-keys.json` (committed, clearly marked test-only) is what the
example client uses; generate a separate key file for the server or export
`SSEA_AEAD_KEY_B64` / `SSEA_NONCE_KEY_B64` / `SSEA_KEY_ID`.

## HTTP API

| Method & path | Purpose |
|---|---|
| `POST /v1/streams/begin` | optionally pre-declare a stream |
| `POST /v1/segments` | submit one base64-encoded v1 frame |
| `POST /v1/streams/{id}/finalize` | release after full authentication |
| `GET  /v1/streams/{id}/status` | state, received seqnos, terminator flag |
| `GET  /v1/streams/{id}/result` | download released bytes (only when released) |
| `POST /v1/streams/{id}/abort` | fail and shred a stream |
| `GET  /v1/audit?message_id=` | redacted audit trail |

Every response carries the `x-request-id` correlation header (send your own,
or use the generated one). Error bodies use a stable envelope:

```json
{"category": "auth_failed", "error": "AES-GCM authentication failed",
 "request_id": "req-…", "state": {"seqno": 2}}
```

Failure categories: `protocoding`, `nonce_conflict`, `dup_segment`,
`reorder`, `truncation`, `auth_failed`, `state`, `incomplete`, `limit`,
`storage`, `key_material`.

## Tests

```bash
pytest -q                      # 85+ tests
pytest --cov=app --cov-report=term-missing
```

The suite covers, with concrete assertions (not "endpoint is callable"):

* six synthetic messages across different chunk sizes — released bytes are
  asserted equal to the committed plaintext and to the independent verifier's
  output using the **other** AEAD library;
* out-of-order delivery;
* segment deletion / replayed frames / terminator moved into the middle;
* missing terminator flag; ciphertext and header tampering;
* retry idempotency and **nonce reuse with different content → rejected**;
* process interruption (new process over the same SQLite + staging), both
  with intact staging and with staging wiped;
* HTTP happy path and every failure category/status code; rate limiting;
* a raw AES-256-GCM known-answer file reproduced byte-identical by both
  backends.

Expected answers come from committed fixture files generated out of band by
`scripts/generate_fixtures.py` — the fixtures are not produced by the
receiving service under test.

## Reproducibility

Exact dependency pins live in `requirements.txt`; the virtualenv is local and
declarable. Recorded, reviewable runs are saved under `docs/`
(see [docs/REPRODUCE.md](docs/REPRODUCE.md)).
