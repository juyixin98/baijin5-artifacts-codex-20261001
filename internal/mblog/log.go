// Package mblog provides structured JSON-lines logging for the fixture.
// Every record carries a run ID (random per process, from crypto/rand) and,
// for request-scoped events, the transaction identity (txID, unit, function)
// plus a short SHA-256 fingerprint of the raw frame, so a request can be
// traced through reception, decision and response in the log.
package mblog

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

// Logger writes one JSON object per line. It is safe for concurrent use.
type Logger struct {
	mu   sync.Mutex
	w    io.Writer
	run  string
	comp string
}

// New creates a logger writing to w. component names the module
// ("server", "client", ...) and appears in every record.
func New(w io.Writer, component string) *Logger {
	return &Logger{w: w, run: runID(), comp: component}
}

// Default returns a logger on stderr.
func Default(component string) *Logger { return New(os.Stderr, component) }

// runID returns 8 random hex characters identifying this process run.
func runID() string {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "norand00"
	}
	return hex.EncodeToString(b[:])
}

// FrameFingerprint returns the first 12 hex chars of the SHA-256 of a raw
// frame. It lets a log reader correlate a received request with the emitted
// response without dumping full payloads.
func FrameFingerprint(frame []byte) string {
	sum := sha256.Sum256(frame)
	return hex.EncodeToString(sum[:6])
}

// Event logs a structured record. Fields are emitted in the order given;
// keys should be snake_case.
func (l *Logger) Event(event string, kv ...any) {
	rec := map[string]any{
		"ts":   time.Now().UTC().Format(time.RFC3339Nano),
		"run":  l.run,
		"comp": l.comp,
		"ev":   event,
	}
	for i := 0; i+1 < len(kv); i += 2 {
		rec[fmt.Sprint(kv[i])] = kv[i+1]
	}
	b, err := json.Marshal(rec)
	if err != nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.w.Write(append(b, '\n'))
}

// RunID exposes the process run identifier.
func (l *Logger) RunID() string { return l.run }
