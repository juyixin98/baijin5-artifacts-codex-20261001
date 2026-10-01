// Package logx provides structured, request-correlated logging.
//
// Every log line is a self-contained JSON object that carries the request
// identity, protocol version, processing location and lifecycle stage.
// Deterministic failures and uncertain conclusions are kept in separate
// fields (Result / Reason) so they can never be confused when reading logs.
package logx

import (
	"encoding/json"
	"io"
	"os"
	"sync"
	"time"
)

// Result values classify how a stage ended.
const (
	ResultOK        = "ok"
	ResultFail      = "fail"
	ResultUncertain = "uncertain"
)

// Event is one structured log record.
type Event struct {
	Time      string         `json:"ts"`
	Level     string         `json:"level"`
	Event     string         `json:"event"`
	Version   string         `json:"version"`
	At        string         `json:"at"`
	ReqID     string         `json:"req_id,omitempty"`
	Stage     string         `json:"stage,omitempty"`
	Client    string         `json:"client,omitempty"`
	Target    string         `json:"target,omitempty"`
	Result    string         `json:"result,omitempty"`
	Reason    string         `json:"reason_code,omitempty"`
	Detail    string         `json:"detail,omitempty"`
	ElapsedMS int64          `json:"elapsed_ms,omitempty"`
	BytesUp   int64          `json:"bytes_to_upstream,omitempty"`
	BytesDown int64          `json:"bytes_to_client,omitempty"`
	Extra     map[string]any `json:"extra,omitempty"`
}

// Logger emits JSON lines, serialized through a mutex so concurrent
// connections never interleave partial records.
type Logger struct {
	mu         sync.Mutex
	w          io.Writer
	host       string
	appVersion string
}

// New creates a logger writing JSON lines to w.
func New(w io.Writer, appVersion string) *Logger {
	host, err := os.Hostname()
	if err != nil {
		host = "unknown-host"
	}
	return &Logger{w: w, host: host, appVersion: appVersion}
}

// Root returns a request logger without a correlation id (startup/shutdown).
func (l *Logger) Root() *RequestLogger {
	return &RequestLogger{l: l}
}

// Sub returns a request logger bound to one proxied connection.
func (l *Logger) Sub(reqID, client, target string) *RequestLogger {
	return &RequestLogger{l: l, reqID: reqID, client: client, target: target}
}

// RequestLogger is a Logger pre-bound to a request identity.
type RequestLogger struct {
	l      *Logger
	reqID  string
	client string
	target string
}

// WithTarget derives a child logger carrying the resolved target string.
func (r *RequestLogger) WithTarget(target string) *RequestLogger {
	cp := *r
	cp.target = target
	return &cp
}

func (r *RequestLogger) emit(level, event, stage, result, reason, detail string, kvs []any) {
	ev := Event{
		Time:    time.Now().UTC().Format(time.RFC3339Nano),
		Level:   level,
		Event:   event,
		Version: r.l.appVersion,
		At:      r.l.host,
		ReqID:   r.reqID,
		Stage:   stage,
		Client:  r.client,
		Target:  r.target,
		Result:  result,
		Reason:  reason,
		Detail:  detail,
	}
	for i := 0; i+1 < len(kvs); i += 2 {
		key, _ := kvs[i].(string)
		if key == "" {
			continue
		}
		if ev.Extra == nil {
			ev.Extra = map[string]any{}
		}
		ev.Extra[key] = kvs[i+1]
	}
	line, err := json.Marshal(ev)
	if err != nil {
		return
	}
	line = append(line, '\n')
	r.l.mu.Lock()
	defer r.l.mu.Unlock()
	_, _ = r.l.w.Write(line)
}

// Info records a successful or informational event.
func (r *RequestLogger) Info(event, stage string, kvs ...any) {
	r.emit("info", event, stage, ResultOK, "", "", kvs)
}

// Fail records a deterministic failure with a machine-readable reason code.
func (r *RequestLogger) Fail(event, stage, reason, detail string, kvs ...any) {
	r.emit("warn", event, stage, ResultFail, reason, detail, kvs)
}

// Uncertain records an ambiguous outcome (for example a transport reset
// after a successful CONNECT, where forwarded-byte completeness is unknown).
func (r *RequestLogger) Uncertain(event, stage, reason, detail string, kvs ...any) {
	r.emit("warn", event, stage, ResultUncertain, reason, detail, kvs)
}
