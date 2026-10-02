// Package logx provides the structured logger used by the service. Every
// log line carries a run id and (for codec operations) a hash of the
// input, so a failure can be traced back to the exact bytes processed.
// At debug level the parser emits one line per state-machine step
// (identifier, length, child enter/exit, EOC match) including its offset
// and the rule applied.
package logx

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"berconf/internal/ber"
)

type Level int

const (
	LevelDebug Level = iota
	LevelInfo
	LevelWarn
	LevelError
)

func ParseLevel(s string) (Level, error) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "", "debug":
		return LevelDebug, nil
	case "info":
		return LevelInfo, nil
	case "warn", "warning":
		return LevelWarn, nil
	case "error":
		return LevelError, nil
	default:
		return LevelInfo, fmt.Errorf("unknown log level %q", s)
	}
}

// Logger is a concurrency-safe structured logger. Children created with
// With carry immutable fields on every emitted line.
type Logger struct {
	mu      sync.Mutex
	out     io.Writer
	level   Level
	jsonFmt bool
	fields  map[string]any
	version string
}

func New(outputPath, format string, level Level, version string) (*Logger, error) {
	var out io.Writer
	switch outputPath {
	case "", "stdout":
		out = os.Stdout
	case "stderr":
		out = os.Stderr
	default:
		if dir := filepath.Dir(outputPath); dir != "." {
			if err := os.MkdirAll(dir, 0o750); err != nil {
				return nil, fmt.Errorf("create log directory: %w", err)
			}
		}
		f, err := os.OpenFile(outputPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
		if err != nil {
			return nil, fmt.Errorf("open log file: %w", err)
		}
		out = f
	}
	return &Logger{
		out:     out,
		level:   level,
		jsonFmt: format != "text",
		fields:  map[string]any{"service_version": version},
		version: version,
	}, nil
}

// With returns a child logger that always attaches fields.
func (l *Logger) With(fields map[string]any) *Logger {
	merged := make(map[string]any, len(l.fields)+len(fields))
	for k, v := range l.fields {
		merged[k] = v
	}
	for k, v := range fields {
		merged[k] = v
	}
	return &Logger{out: l.out, level: l.level, jsonFmt: l.jsonFmt, fields: merged, version: l.version}
}

func (l *Logger) log(level Level, name string, msg string, fields map[string]any) {
	if level < l.level {
		return
	}
	all := make(map[string]any, len(l.fields)+len(fields)+3)
	for k, v := range l.fields {
		all[k] = v
	}
	for k, v := range fields {
		all[k] = v
	}
	all["ts"] = time.Now().UTC().Format(time.RFC3339Nano)
	all["level"] = name
	all["msg"] = msg

	l.mu.Lock()
	defer l.mu.Unlock()
	if l.jsonFmt {
		enc := json.NewEncoder(l.out)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(all)
		return
	}
	parts := []string{all["ts"].(string), strings.ToUpper(name)}
	if rid, ok := all["run_id"]; ok {
		parts = append(parts, fmt.Sprintf("run=%s", rid))
	}
	parts = append(parts, msg)
	for k, v := range fields {
		if k == "run_id" {
			continue
		}
		parts = append(parts, fmt.Sprintf("%s=%v", k, v))
	}
	fmt.Fprintln(l.out, strings.Join(parts, " "))
}

func (l *Logger) Debug(msg string, fields map[string]any) { l.log(LevelDebug, "debug", msg, fields) }
func (l *Logger) Info(msg string, fields map[string]any)  { l.log(LevelInfo, "info", msg, fields) }
func (l *Logger) Warn(msg string, fields map[string]any)  { l.log(LevelWarn, "warn", msg, fields) }
func (l *Logger) Error(msg string, fields map[string]any) { l.log(LevelError, "error", msg, fields) }

// Tracer adapts the parser event stream into debug log lines. The run id
// and input identity are attached via fields.
func (l *Logger) Tracer(runID string, seq *int, mu *sync.Mutex) ber.Tracer {
	return func(s ber.Step) {
		mu.Lock()
		*seq++
		n := *seq
		mu.Unlock()
		l.Debug("parser step", map[string]any{
			"run_id": runID,
			"step":   n,
			"phase":  s.Name,
			"offset": s.Offset,
			"depth":  s.Depth,
			"detail": s.Detail,
			"rule":   ruleFor(s.Name),
		})
	}
}

// ruleFor states the判定 basis documented for each parser phase.
func ruleFor(phase string) string {
	switch phase {
	case "start":
		return "X.690 8: begin decode of one top-level TLV"
	case "tag":
		return "X.690 8.1.2: identifier octets, class/P-C/tag"
	case "length":
		return "X.690 8.1.3: definite short/long or indefinite form, bounded"
	case "enter":
		return "state machine: dispatch primitive vs constructed"
	case "child":
		return "state machine: sibling accepted, span rechecked against limit"
	case "eoc":
		return "X.690 8.1.5: EOC closes exactly one indefinite construction"
	default:
		return "profile rule"
	}
}

// NewRunID returns 16 random bytes as hex (32 chars).
func NewRunID() string {
	var b [16]byte
	if _, err := readRand(b[:]); err != nil {
		// Falling back to time would weaken identity; fail loudly in logs
		// by marking the id. crypto/rand failures are exceptional.
		return "0000000000000000-rand-failed"
	}
	return hex.EncodeToString(b[:])
}

// InputFingerprint identifies the exact bytes processed.
func InputFingerprint(raw []byte) map[string]any {
	sum := sha256Hex(raw)
	preview := 64
	if len(raw) < preview {
		preview = len(raw)
	}
	return map[string]any{
		"input_len":      len(raw),
		"input_sha256":   sum,
		"input_hex_head": hex.EncodeToString(raw[:preview]),
	}
}
