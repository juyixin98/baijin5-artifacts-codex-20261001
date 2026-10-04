// Package logx produces run-correlated step logs. Every line carries the
// run identity and tool version so test logs can be tied to an input and
// show progress and the basis for each verdict.
package logx

import (
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

// Version is the fixed tool version reported in every log stream.
const Version = "1.0.0"

// Logger is the minimal structured step logger used across the pipeline.
type Logger struct {
	mu     sync.Mutex
	w      io.Writer
	runID  string
	step   int
	prefix string
}

// New creates a logger for the given run identity. Passing "" derives one
// from the current time so manual CLI runs are still correlated.
func New(w io.Writer, runID string) *Logger {
	if w == nil {
		w = io.Discard
	}
	if runID == "" {
		runID = "run-" + time.Now().Format("20060102-150405.000")
	}
	return &Logger{w: w, runID: runID}
}

// RunID reports the identity carried by this logger.
func (l *Logger) RunID() string { return l.runID }

// Step logs one pipeline step with its inputs and decision basis.
func (l *Logger) Step(stage, detail, basis string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.step++
	scope := l.prefix
	if scope != "" {
		scope = scope + " "
	}
	fmt.Fprintf(l.w, "run=%s v=%s step=%02d %s%s | %s", l.runID, Version, l.step, scope, stage, detail)
	if basis != "" {
		fmt.Fprintf(l.w, " | basis: %s", basis)
	}
	fmt.Fprintln(l.w)
}

// Warn logs a non-fatal condition without incrementing the step counter.
func (l *Logger) Warn(code, detail string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	fmt.Fprintf(l.w, "run=%s v=%s WARN %s | %s\n", l.runID, Version, code, detail)
}

// WithPrefix returns a child logger that tags every line (used per object).
func (l *Logger) WithPrefix(prefix string) *Logger {
	return &Logger{w: l.w, runID: l.runID, prefix: prefix, step: 0}
}

// TeeFile mirrors log output to a file under test-logs in addition to w.
// It is a convenience for the verification script and integration tests.
func TeeFile(w io.Writer, path, runID string) (*Logger, func() error, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, nil, err
	}
	if w == nil {
		w = io.Discard
	}
	return New(io.MultiWriter(w, f), runID), f.Close, nil
}
