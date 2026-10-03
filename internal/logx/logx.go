// Package logx provides identity-aware, step-logging used by tests and CLI.
// Every line carries a run identity so output can be correlated with an
// input case or test invocation. Unknown/error states are logged explicitly
// rather than collapsed into success.
package logx

import (
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

// Level controls log verbosity.
type Level int

const (
	LevelQuiet Level = iota
	LevelInfo
	LevelDebug
)

// Logger writes prefixed, mutex-protected log lines.
type Logger struct {
	mu   sync.Mutex
	w    io.Writer
	id   string
	lvl  Level
	step int
}

// New creates a logger bound to a run identity.
func New(w io.Writer, id string, lvl Level) *Logger {
	if w == nil {
		w = io.Discard
	}
	return &Logger{w: w, id: id, lvl: lvl}
}

// Default returns a stderr info-level logger.
func Default(id string) *Logger { return New(os.Stderr, id, LevelInfo) }

// Nop returns a discarding logger.
func Nop() *Logger { return New(io.Discard, "-", LevelQuiet) }

// ID returns the run identity.
func (l *Logger) ID() string { return l.id }

func (l *Logger) log(tag, format string, args ...any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	ts := time.Now().Format("15:04:05.000")
	fmt.Fprintf(l.w, "[%s] [%s] [%s] %s\n", ts, l.id, tag, fmt.Sprintf(format, args...))
}

// Info logs at info level.
func (l *Logger) Info(format string, args ...any) {
	if l.lvl >= LevelInfo {
		l.log("INFO", format, args...)
	}
}

// Debug logs computation steps at debug level.
func (l *Logger) Debug(format string, args ...any) {
	if l.lvl >= LevelDebug {
		l.log("DEBUG", format, args...)
	}
}

// Step logs a numbered progress step and returns the step number.
func (l *Logger) Step(format string, args ...any) int {
	l.step++
	if l.lvl >= LevelInfo {
		l.log(fmt.Sprintf("STEP%d", l.step), format, args...)
	}
	return l.step
}

// Verdict logs the final decision basis.
func (l *Logger) Verdict(ok bool, format string, args ...any) {
	tag := "PASS"
	if !ok {
		tag = "FAIL"
	}
	if l.lvl >= LevelInfo {
		l.log(tag, format, args...)
	}
}

// Warn logs an explicit non-fatal anomaly.
func (l *Logger) Warn(format string, args ...any) {
	l.log("WARN", format, args...)
}

// Errorf logs an explicit failure or unknown state.
func (l *Logger) Errorf(format string, args ...any) {
	l.log("ERROR", format, args...)
}
