package difftest

import (
	"encoding/json"
	"io"
	"sync"
	"time"
)

// Logger writes structured, replay-ready records to an io.Writer as JSONL.
type Logger struct {
	mu sync.Mutex
	w  io.Writer
	enc *json.Encoder
}

func NewLogger(w io.Writer) *Logger {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return &Logger{w: w, enc: enc}
}

// Header records run environment metadata once.
func (l *Logger) Header(suite string) {
	l.write(map[string]any{
		"type":  "header",
		"suite": suite,
		"ts":    time.Now().Format(time.RFC3339Nano),
	})
}

// Script records a script definition before execution (replay source).
func (l *Logger) Script(rep *Report) {
	l.write(map[string]any{
		"type":   "script",
		"name":   rep.Name,
		"source": rep.Source,
	})
}

// Report writes a per-script summary plus every event (run number, action,
// both engines' observations, statuses, match flag and reason).
func (l *Logger) Report(rep *Report) {
	l.write(map[string]any{
		"type":        "script_result",
		"name":        rep.Name,
		"runs":        rep.Runs,
		"passed":      rep.Passed,
		"first_error": rep.FirstErr,
		"events":      rep.Events,
	})
}

// Note records a free-form milestone (build, category boundary, etc.).
func (l *Logger) Note(kind, msg string) {
	l.write(map[string]any{"type": "note", "kind": kind, "msg": msg, "ts": time.Now().Format(time.RFC3339Nano)})
}

func (l *Logger) write(v any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = l.enc.Encode(v)
}
