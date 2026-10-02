// Package observ provides structured, run-correlated event logging. Every
// event carries the run id, an input correlation hash, the engine version and a
// monotonic step number, so test logs can always be tied back to the exact
// input and specialization decision ("basis") that produced them.
package observ

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"runtime/debug"
	"strconv"
	"sync"
	"time"
)

// Version is the engine/algorithm version reported in every event.
const Version = "funcspec-1.0.0"

// GoVersion is captured once at init.
var GoVersion = "unknown"

func init() {
	if info, ok := debug.ReadBuildInfo(); ok {
		GoVersion = info.GoVersion
	}
}

// Event is one structured log record.
type Event struct {
	Type    string         `json:"type"`
	RunID   string         `json:"run_id"`
	InputID string         `json:"input_id"`
	Version string         `json:"version"`
	Step    int            `json:"step"`
	TimeMs  int64          `json:"time_ms"`
	Basis   string         `json:"basis,omitempty"`
	Detail  map[string]any `json:"detail,omitempty"`
}

// Logger writes correlated JSON events.
type Logger struct {
	mu      sync.Mutex
	w       io.Writer
	runID   string
	inputID string
	step    int
	start   time.Time
}

// New creates a logger. runID and inputID are copied onto every event.
func New(w io.Writer, runID, inputID string) *Logger {
	if w == nil {
		w = io.Discard
	}
	return &Logger{w: w, runID: runID, inputID: inputID, start: time.Now()}
}

// NewRun builds a run id and an input correlation hash deterministically.
func NewRun(entry string, source []byte, argv []int64) (runID, inputID string) {
	h := sha256.New()
	h.Write([]byte(entry))
	h.Write([]byte{0})
	h.Write(source)
	h.Write([]byte{0})
	for _, a := range argv {
		h.Write([]byte(strconv.FormatInt(a, 10)))
		h.Write([]byte{','})
	}
	sum := hex.EncodeToString(h.Sum(nil))
	return sum[:12], sum[:16]
}

// Event emits one record. basis explains the decision criterion; detail holds
// structured computation/progress data.
func (l *Logger) Event(typ, basis string, detail map[string]any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.step++
	ev := Event{
		Type:    typ,
		RunID:   l.runID,
		InputID: l.inputID,
		Version: Version,
		Step:    l.step,
		TimeMs:  time.Since(l.start).Microseconds() / 1000,
		Basis:   basis,
		Detail:  detail,
	}
	enc := json.NewEncoder(l.w)
	_ = enc.Encode(ev)
}

// StdLogger writes to a file or stdout ("-").
func StdLogger(path, runID, inputID string) (*Logger, func() error, error) {
	if path == "" {
		return New(io.Discard, runID, inputID), func() error { return nil }, nil
	}
	if path == "-" {
		return New(os.Stdout, runID, inputID), func() error { return nil }, nil
	}
	f, err := os.Create(path)
	if err != nil {
		return nil, nil, fmt.Errorf("create log %s: %w", path, err)
	}
	return New(f, runID, inputID), f.Close, nil
}
