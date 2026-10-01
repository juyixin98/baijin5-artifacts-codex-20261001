// Package logging implements a small structured JSON-lines logger used to
// correlate runs with their inputs, specialization steps and decisions.
package logging

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"strconv"
	"sync"
	"time"

	"funcspect/internal/version"
)

// Logger emits one JSON object per event.
type Logger struct {
	mu  sync.Mutex
	w   io.Writer
	on  bool
	now func() time.Time
}

// New creates a logger writing to w; when enabled is false every event is dropped.
func New(w io.Writer, enabled bool) *Logger {
	if w == nil {
		w = io.Discard
	}
	return &Logger{w: w, on: enabled, now: time.Now}
}

func Discard() *Logger { return New(io.Discard, false) }

func (l *Logger) Enabled() bool { return l != nil && l.on }

// NewRunID returns a short random run identifier.
func NewRunID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return hex.EncodeToString(b[:])
}

// Event records a structured log line.
func (l *Logger) Event(level, kind, runID string, fields map[string]any) {
	if l == nil || !l.on {
		return
	}
	if fields == nil {
		fields = map[string]any{}
	}
	fields["ts"] = l.now().UTC().Format(time.RFC3339Nano)
	fields["level"] = level
	fields["event"] = kind
	if runID != "" {
		fields["run_id"] = runID
	}
	fields["version"] = version.Version
	fields["go"] = version.GoRuntime()
	l.mu.Lock()
	defer l.mu.Unlock()
	enc := json.NewEncoder(l.w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(fields)
}

func (l *Logger) Info(kind, runID string, fields map[string]any) {
	l.Event("info", kind, runID, fields)
}

func (l *Logger) Debug(kind, runID string, fields map[string]any) {
	l.Event("debug", kind, runID, fields)
}

func (l *Logger) Warn(kind, runID string, fields map[string]any) {
	l.Event("warn", kind, runID, fields)
}

func (l *Logger) Error(kind, runID string, fields map[string]any) {
	l.Event("error", kind, runID, fields)
}

// StdEnabled reports whether logging should default on for the CLI.
func StdEnabled() bool {
	v := os.Getenv("FUNCSPECT_QUIET")
	return v == "" || v == "0" || v == "false"
}
