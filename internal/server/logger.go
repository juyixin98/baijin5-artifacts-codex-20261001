package server

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

// Level is a log severity.
type Level int

const (
	LevelDebug Level = iota
	LevelInfo
	LevelWarn
	LevelError
)

func parseLevel(s string) Level {
	switch s {
	case "debug":
		return LevelDebug
	case "warn":
		return LevelWarn
	case "error":
		return LevelError
	default:
		return LevelInfo
	}
}

// Fields are structured key/value pairs attached to one event.
type Fields map[string]any

// Logger emits explainable, request-correlated log lines. Every event from
// a session carries the same request_id, plus the SOCKS5 version, the
// processing step/location and, when relevant, separate failure_category,
// failure_reason and uncertainty fields.
type Logger struct {
	mu         sync.Mutex
	w          io.Writer
	min        Level
	textFormat bool
}

// NewLogger builds a logger writing to w.
func NewLogger(w io.Writer, level string, textFormat bool) *Logger {
	if w == nil {
		w = os.Stderr
	}
	return &Logger{w: w, min: parseLevel(level), textFormat: textFormat}
}

func (l *Logger) log(level Level, event string, f Fields) {
	if level < l.min {
		return
	}
	ts := time.Now().UTC().Format(time.RFC3339Nano)
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.textFormat {
		fmt.Fprintf(l.w, "%s %-5s %s", ts, levelName(level), event)
		for k, v := range f {
			fmt.Fprintf(l.w, " %s=%v", k, v)
		}
		fmt.Fprintln(l.w)
		return
	}
	rec := map[string]any{"ts": ts, "level": levelName(level), "event": event}
	for k, v := range f {
		rec[k] = v
	}
	enc := json.NewEncoder(l.w)
	_ = enc.Encode(rec)
}

func levelName(l Level) string {
	switch l {
	case LevelDebug:
		return "debug"
	case LevelWarn:
		return "warn"
	case LevelError:
		return "error"
	default:
		return "info"
	}
}

// Debug/Info/Warn/Error emit one event.
func (l *Logger) Debug(event string, f Fields) { l.log(LevelDebug, event, f) }
func (l *Logger) Info(event string, f Fields)  { l.log(LevelInfo, event, f) }
func (l *Logger) Warn(event string, f Fields)  { l.log(LevelWarn, event, f) }
func (l *Logger) Error(event string, f Fields) { l.log(LevelError, event, f) }

// WithRequest returns a small helper that stamps every event with the
// request correlation fields.
func (l *Logger) WithRequest(id, client string) *RequestLogger {
	return &RequestLogger{Logger: l, ID: id, Client: client}
}

// RequestLogger stamps request_id/client_addr on every event.
type RequestLogger struct {
	*Logger
	ID     string
	Client string
}

func (r *RequestLogger) at(f Fields) Fields {
	if f == nil {
		f = Fields{}
	}
	f["request_id"] = r.ID
	f["client_addr"] = r.Client
	f["socks_version"] = "RFC1928/v5"
	return f
}

// Step logs a normal protocol step.
func (r *RequestLogger) Step(event, location string, f Fields) {
	f = r.at(f)
	f["step"] = event
	f["location"] = location
	r.Info("socks5_"+event, f)
}

// Failure logs a categorized failure with the reason and any uncertainty in
// dedicated fields, so an operator never has to infer them from prose.
func (r *RequestLogger) Failure(location string, category, reason, uncertainty string, f Fields) {
	f = r.at(f)
	f["location"] = location
	f["failure_category"] = category
	f["failure_reason"] = reason
	if uncertainty != "" {
		f["uncertainty"] = uncertainty
	}
	level := r.Warn
	if category == "internal_error" || category == "auth_backend_error" {
		level = r.Error
	}
	level("socks5_failure", f)
}
