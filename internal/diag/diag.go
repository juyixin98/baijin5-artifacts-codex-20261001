// Package diag produces structured, correlation-ID tagged diagnostics for
// fingerprint and invalidation decisions. Sensitive constant values are never
// printed verbatim; they are masked.
package diag

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"sync"
	"time"
)

// Decision is the verdict for one checked item.
type Decision string

const (
	Accept       Decision = "accept"       // fingerprint reusable / change safe
	Reject       Decision = "reject"       // invalidated / must recompile
	Inconclusive Decision = "inconclusive" // cannot determine (version gate etc.)
)

// Entry is one structured diagnostic record.
type Entry struct {
	TS       string            `json:"ts"`
	Level    string            `json:"level"`
	Request  string            `json:"request_id"`
	Record   string            `json:"record_id"`
	Decision Decision          `json:"decision"`
	Subject  string            `json:"subject"`
	Reason   string            `json:"reason"`
	State    map[string]string `json:"state,omitempty"`
}

// Logger writes JSON-lines diagnostics and tracks a request id.
type Logger struct {
	mu      sync.Mutex
	w       io.Writer
	request string
	seq     int
	enabled bool
	entries []Entry
}

// Entries returns all records produced on this logger.
func (l *Logger) Entries() []Entry {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]Entry, len(l.entries))
	copy(out, l.entries)
	return out
}

// NewLogger builds a logger. If w is nil it defaults to stderr; set enabled
// false to suppress output while still collecting entries via Entries().
func NewLogger(w io.Writer, requestID string, enabled bool) *Logger {
	if w == nil {
		w = os.Stderr
	}
	if requestID == "" {
		requestID = newID("req")
	}
	return &Logger{w: w, request: requestID, enabled: enabled}
}

// RequestID returns the correlation id used for all entries.
func (l *Logger) RequestID() string { return l.request }

func (l *Logger) nextRecord() string {
	l.seq++
	return l.request + "-r" + pad(l.seq)
}

func pad(n int) string {
	if n < 10 {
		return "0" + itoa(n)
	}
	return itoa(n)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

// Log records and (if enabled) writes one diagnostic entry.
func (l *Logger) Log(level string, d Decision, subject, reason string, state map[string]string) Entry {
	l.mu.Lock()
	defer l.mu.Unlock()
	e := Entry{
		TS: time.Now().UTC().Format(time.RFC3339Nano), Level: level,
		Request: l.request, Record: l.nextRecord(),
		Decision: d, Subject: subject, Reason: reason, State: state,
	}
	if l.enabled {
		b, _ := json.Marshal(e)
		l.w.Write(append(b, '\n'))
	}
	l.entries = append(l.entries, e)
	return e
}

// entries collected even when output is suppressed.
// MaskValue redacts a sensitive literal value for logging.
func MaskValue(kind, value string) string {
	if value == "" {
		return "<redacted empty>"
	}
	// show only the kind and a short length fingerprint, never content
	return "<redacted:" + kind + ":len=" + itoa(len(value)) + ">"
}

func newID(prefix string) string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return prefix + "_" + hex.EncodeToString(b[:])
}
