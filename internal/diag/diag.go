// Package diag provides structured diagnostics with request/record identity,
// key build state, an accept/reject/undecidable verdict and redaction of
// potentially sensitive literal data.
package diag

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"time"
)

type Verdict string

const (
	VerdictAccepted    Verdict = "accepted"
	VerdictRejected    Verdict = "rejected"
	VerdictUndecidable Verdict = "undecidable"
)

type Category string

const (
	CatLex            Category = "lex_error"
	CatParse          Category = "parse_error"
	CatType           Category = "type_error"
	CatUnresolved     Category = "unresolved_symbol"
	CatFingerprint    Category = "fingerprint_changed"
	CatStaleSemVer    Category = "stale_semantic_version"
	CatCacheCorrupt   Category = "cache_corrupt"
	CatGeneric        Category = "generic_instantiation"
	CatRuntime        Category = "runtime_error"
	CatSemanticChange Category = "semantic_change"
	CatPrivateBody    Category = "private_body_change"
)

type Entry struct {
	Seq      int            `json:"seq"`
	Time     string         `json:"time"`
	Request  string         `json:"request_id"`
	Module   string         `json:"module,omitempty"`
	Symbol   string         `json:"symbol,omitempty"`
	Verdict  Verdict        `json:"verdict"`
	Category Category       `json:"category"`
	Message  string         `json:"message"`
	State    map[string]any `json:"state,omitempty"`
}

type Logger struct {
	mu      sync.Mutex
	out     io.Writer
	request string
	seq     int
	module  string
}

func NewRequestID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return "req-" + hex.EncodeToString(b[:])
}

func NewLogger(w io.Writer, requestID, module string) *Logger {
	if w == nil {
		w = io.Discard
	}
	if requestID == "" {
		requestID = NewRequestID()
	}
	return &Logger{out: w, request: requestID, module: module}
}

func (l *Logger) WithModule(module string) *Logger {
	return &Logger{out: l.out, request: l.request, module: module, seq: l.seq}
}

func (l *Logger) RequestID() string { return l.request }

func (l *Logger) log(v Verdict, cat Category, symbol, msg string, state map[string]any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.seq++
	e := Entry{
		Seq:      l.seq,
		Time:     time.Now().UTC().Format(time.RFC3339Nano),
		Request:  l.request,
		Module:   l.module,
		Symbol:   symbol,
		Verdict:  v,
		Category: cat,
		Message:  msg,
		State:    state,
	}
	fmt.Fprintf(l.out, "[%s] %s req=%s mod=%s sym=%s cat=%s %s",
		e.Time, e.Verdict, e.Request, dash(e.Module), dash(e.Symbol), e.Category, e.Message)
	if len(e.State) > 0 {
		parts := make([]string, 0, len(e.State))
		for k, val := range e.State {
			parts = append(parts, fmt.Sprintf("%s=%v", k, val))
		}
		fmt.Fprintf(l.out, " {%s}", strings.Join(parts, " "))
	}
	fmt.Fprintln(l.out)
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func (l *Logger) Accept(symbol, msg string, state map[string]any) {
	l.log(VerdictAccepted, "", symbol, msg, state)
}
func (l *Logger) AcceptAs(cat Category, symbol, msg string, state map[string]any) {
	l.log(VerdictAccepted, cat, symbol, msg, state)
}
func (l *Logger) Reject(cat Category, symbol, msg string, state map[string]any) {
	l.log(VerdictRejected, cat, symbol, msg, state)
}
func (l *Logger) Undecidable(cat Category, symbol, msg string, state map[string]any) {
	l.log(VerdictUndecidable, cat, symbol, msg, state)
}

// Redact masks a literal for diagnostic output: keeps a short prefix of a
// string and its length; non-string values are shown by type only.
func Redact(raw string) string {
	if raw == "" {
		return "<empty>"
	}
	const keep = 2
	pref := raw
	if len(raw) > keep {
		pref = raw[:keep]
	}
	return fmt.Sprintf("%q***(len=%d)", pref, len(raw))
}

func StdLogger(requestID string) *Logger {
	return NewLogger(os.Stderr, requestID, "")
}
