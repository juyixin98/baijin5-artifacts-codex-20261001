// Package diag provides structured diagnostic logging that always carries a
// correlation reference (Message ID and/or Token, plus an exchange ref) and
// key protocol state. Sensitive material (payloads, raw tokens) is never
// printed verbatim: payloads render as length plus a short hash, tokens as a
// truncated hex prefix.
package diag

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"os"
	"sync"
	"time"
)

// Ref correlates a log line to a CoAP exchange. MessageID keys the
// message-level exchange (deduplication, retransmission); TokenHex keys the
// request/response association. They are kept separate by design.
type Ref struct {
	MessageID *uint16
	TokenHex  string
	Exchange  string
	Peer      string
}

func (r Ref) attrs() []any {
	var attrs []any
	if r.Exchange != "" {
		attrs = append(attrs, "ex", r.Exchange)
	}
	if r.MessageID != nil {
		attrs = append(attrs, "mid", fmt.Sprintf("0x%04x", *r.MessageID))
	}
	if r.TokenHex != "" {
		attrs = append(attrs, "token", MaskTokenHex(r.TokenHex))
	}
	if r.Peer != "" {
		attrs = append(attrs, "peer", r.Peer)
	}
	return attrs
}

// Logger is the small logging surface the protocol packages depend on.
type Logger interface {
	Debug(r Ref, msg string, kv ...any)
	Info(r Ref, msg string, kv ...any)
	Warn(r Ref, msg string, kv ...any)
	Error(r Ref, msg string, kv ...any)
}

// Level controls logger verbosity.
type Level slog.Level

// Supported levels.
const (
	LevelDebug = Level(slog.LevelDebug)
	LevelInfo  = Level(slog.LevelInfo)
	LevelWarn  = Level(slog.LevelWarn)
	LevelError = Level(slog.LevelError)
)

type logger struct {
	h   slog.Handler
	mu  *sync.Mutex
	out io.Writer
}

// NewJSONLogger emits JSON lines to w at the given level.
func NewJSONLogger(w io.Writer, level Level) Logger {
	h := slog.NewJSONHandler(w, &slog.HandlerOptions{Level: slog.Level(level)})
	return &logger{h: h, mu: &sync.Mutex{}, out: w}
}

// NewTextLogger emits human-readable lines to w at the given level.
func NewTextLogger(w io.Writer, level Level) Logger {
	h := slog.NewTextHandler(w, &slog.HandlerOptions{Level: slog.Level(level)})
	return &logger{h: h, mu: &sync.Mutex{}, out: w}
}

// DefaultLogger writes text at INFO to stderr.
func DefaultLogger() Logger { return NewTextLogger(os.Stderr, LevelInfo) }

// Discard returns a logger that drops everything (tests).
func Discard() Logger {
	return NewTextLogger(io.Discard, LevelError)
}

func (l *logger) log(slogLevel slog.Level, r Ref, msg string, kv []any) {
	rec := slog.NewRecord(time.Now(), slogLevel, msg, 0)
	rec.Add(r.attrs()...)
	rec.Add(sanitize(kv)...)
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = l.h.Handle(nil, rec)
}

func (l *logger) Debug(r Ref, msg string, kv ...any) { l.log(slog.LevelDebug, r, msg, kv) }
func (l *logger) Info(r Ref, msg string, kv ...any)  { l.log(slog.LevelInfo, r, msg, kv) }
func (l *logger) Warn(r Ref, msg string, kv ...any)  { l.log(slog.LevelWarn, r, msg, kv) }
func (l *logger) Error(r Ref, msg string, kv ...any) { l.log(slog.LevelError, r, msg, kv) }

// MaskTokenHex renders only the first two bytes of a hex token. Tokens are not
// secret on their own, but full correlation values are unnecessary in logs.
func MaskTokenHex(tokenHex string) string {
	if len(tokenHex) <= 4 {
		return tokenHex
	}
	return tokenHex[:4] + "…"
}

// PayloadSummary never prints payload contents: it reports length and the
// first 6 bytes of SHA-256, enough to detect a change without disclosure.
func PayloadSummary(b []byte) string {
	sum := sha256.Sum256(b)
	return fmt.Sprintf("len=%d sha256:%s", len(b), hex.EncodeToString(sum[:6]))
}

// sensitiveKeys are rendered redacted regardless of their value.
var sensitiveKeys = map[string]bool{
	"payload": true, "body": true, "secret": true, "password": true,
	"key": true, "data": true,
}

func sanitize(kv []any) []any {
	out := make([]any, 0, len(kv))
	for i := 0; i < len(kv); i += 2 {
		key, _ := kv[i].(string)
		val := kv[i+1]
		if sensitiveKeys[key] {
			if b, ok := val.([]byte); ok {
				out = append(out, key, PayloadSummary(b))
				continue
			}
			out = append(out, key, "<redacted>")
			continue
		}
		out = append(out, kv[i], kv[i+1])
	}
	return out
}
