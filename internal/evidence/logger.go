// Package evidence provides the run-scoped structured logger used by every
// binary and test harness in the lab. Every line is one JSON object carrying
// run id, monotonic sequence number, component, level, event name and the key
// intermediate state needed to replay a decision. When a *store.Store is
// attached the same records are persisted to SQLite.
package evidence

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sync"
	"time"

	"stunlab/internal/store"
)

// Event is one structured log record.
type Event struct {
	TS        string         `json:"ts"`
	RunID     string         `json:"run_id"`
	Seq       int64          `json:"seq"`
	Component string         `json:"component"`
	Level     string         `json:"level"`
	Event     string         `json:"event"`
	Fields    map[string]any `json:"fields,omitempty"`
}

// Logger serialises events to JSON lines and optionally into SQLite.
type Logger struct {
	mu        sync.Mutex
	runID     string
	component string
	w         io.Writer
	seq       int64
	st        *store.Store
	startedAt string
	note      string
}

// NewRunID returns a sortable, unique run identifier: UTC timestamp + random.
func NewRunID(prefix string) string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return fmt.Sprintf("%s-%s-%s", prefix,
		time.Now().UTC().Format("20060102T150405.000000000"), hex.EncodeToString(b[:]))
}

// NewLogger creates a logger. If w is nil events are only kept in SQLite (when
// attached) / memory.
func NewLogger(runID, component string, w io.Writer, st *store.Store, note string) *Logger {
	l := &Logger{
		runID:     runID,
		component: component,
		w:         w,
		st:        st,
		startedAt: time.Now().UTC().Format(time.RFC3339Nano),
		note:      note,
	}
	if st != nil {
		_ = st.EnsureRun(runID, component, l.startedAt, note)
	}
	return l
}

// RunID returns the run identifier.
func (l *Logger) RunID() string { return l.runID }

func (l *Logger) log(level, event string, fields map[string]any) {
	l.mu.Lock()
	l.seq++
	ev := Event{
		TS:        time.Now().UTC().Format(time.RFC3339Nano),
		RunID:     l.runID,
		Seq:       l.seq,
		Component: l.component,
		Level:     level,
		Event:     event,
		Fields:    fields,
	}
	payload, _ := json.Marshal(ev)
	if l.w != nil {
		_, _ = l.w.Write(append(payload, '\n'))
	}
	if l.st != nil {
		_ = l.st.InsertEvent(ev.RunID, ev.TS, ev.Seq, ev.Component, ev.Level, ev.Event, string(payload))
	}
	l.mu.Unlock()
}

// Info records an informational event.
func (l *Logger) Info(event string, fields map[string]any) { l.log("info", event, fields) }

// Warn records a warning event.
func (l *Logger) Warn(event string, fields map[string]any) { l.log("warn", event, fields) }

// Error records an error event; kind should be one of the stun.ErrorKind
// values (or a module-local equivalent) so failure categories stay distinct.
func (l *Logger) Error(event, kind string, fields map[string]any) {
	if fields == nil {
		fields = map[string]any{}
	}
	fields["error_kind"] = kind
	l.log("error", event, fields)
}

// Decision records a pass/fail judgement with the reason and the evidence
// (actual vs expected) behind it.
func (l *Logger) Decision(name string, passed bool, reason string, fields map[string]any) {
	if fields == nil {
		fields = map[string]any{}
	}
	fields["verdict"] = map[bool]string{true: "pass", false: "fail"}[passed]
	fields["reason"] = reason
	level := "info"
	if !passed {
		level = "error"
	}
	l.log(level, "decision:"+name, fields)
}
