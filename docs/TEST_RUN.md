# Test run record

Date: 2026-10-01
Host: Linux 6.8.0-90-generic, Go 1.22.2 (toolchain auto-selected go1.26.8 for
`modernc.org/sqlite@v1.60.1`), CGO independent (pure-Go SQLite driver).

## Command

```bash
go vet ./...
go test -race ./...
go test -race -cover ./...
```

## Failures observed during development (kept, not hidden)

1. **`MAIL`/`RCPT` keyword not stripped → `501 invalid mailbox syntax`.**
   The state machine passed `FROM:<a@localhost>` straight to the path parser.
   Fixed by adding `stripKeyword` for `FROM:`/`TO:` in `internal/protocol/fsm.go`.
   Affected: `TestSessionBudgets_*`, `TestDataEOFAfterDot*`, all end-to-end
   tests. Re-ran → green.

2. **Over-long command line closed the connection instead of `500`+continue.**
   The server treated resyncable `wire.ErrLineTooLong` as terminal.
   Fixed in `internal/server/server.go` to reply `500` and keep the loop;
   added `TestEndToEnd_OverlongCommandLine` asserting recovery via `NOOP`.

3. **Coverage below the 80% gate** on `config` (75.6%), `storage` (75.9%),
   `server` (70.5%) after the first green run. Added targeted tests
   (validation branches, malformed/missing JSON, open errors & `DB()` handle,
   idle timeout 421, bind failure, idempotent Close, HELO fallback, nil logger,
   fatal overflow 421, capacity 421). Re-ran → all packages ≥ 80%.

## Final result

```
ok  smtpsink/internal/config    coverage: 97.6%
ok  smtpsink/internal/protocol  coverage: 83.6%
ok  smtpsink/internal/server    coverage: 83.9%
ok  smtpsink/internal/storage   coverage: 83.3%
ok  smtpsink/internal/wire      coverage: 81.5%
?   smtpsink/cmd/smtpsink       [no test files]
```

`go vet ./...` clean; `gofmt -l .` empty; race detector: no data races.

## Live smoke run (real loopback TCP + on-disk verification)

`scripts/example-session.sh` against `go run ./cmd/smtpsink`:
duplicate RCPT → `250 ... already listed`; remote RCPT → `550`; DATA →
`250 message accepted as 341b885acdae611dbde6b77e`. Independent read-only DB
query showed exactly one copy (`bob@sink.local`), 67 bytes, body normalized to
CRLF and the leading dot unstuffed. Logs showed redacted addresses
(`s****r@localhost`, `b*b@sink.local`, `e**l@external.example`) with
`session_id`, `state`, `message_id` and accept/reject reasons.

## Not executed / deliberately out of scope

- No external/remote SMTP delivery test exists; the service has no outbound
  socket and config validation rejects non-loopback binds. "No external mail"
  is enforced by construction, not merely untested.
- No TLS/AUTH tests (features intentionally unsupported; answered 502).
- `gosec`/`staticcheck` were not installed in this offline environment and were
  not run; `go vet` (the available analyzer) was run and is clean.
