// Package testlog gives tests a replayable audit trail: every test run
// gets a run id, and key intermediate states plus the reason for each
// judgment are appended as JSON lines to testlogs/<test>.jsonl (override
// the directory with TESTLOG_DIR). The same lines go to `go test -v`
// output via t.Logf.
package testlog

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// Logger is one test's structured log sink.
type Logger struct {
	Run string
	t   *testing.T
	mu  sync.Mutex
	f   *os.File
}

// New creates a Logger for t, generating a fresh run id
// (timestamp + crypto/rand suffix).
func New(t *testing.T) *Logger {
	t.Helper()
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		t.Fatalf("testlog: crypto/rand failed: %v", err)
	}
	run := time.Now().UTC().Format("20060102T150405.000Z") + "-" + hex.EncodeToString(b[:])
	dir := os.Getenv("TESTLOG_DIR")
	if dir == "" {
		dir = "testlogs"
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("testlog: cannot create %s: %v", dir, err)
	}
	name := strings.NewReplacer("/", "_", " ", "_").Replace(t.Name()) + ".jsonl"
	f, err := os.OpenFile(filepath.Join(dir, name), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatalf("testlog: cannot open log: %v", err)
	}
	l := &Logger{Run: run, t: t, f: f}
	l.Log("start", "test run started")
	t.Cleanup(func() {
		l.Log("end", "test run finished")
		l.f.Close()
	})
	return l
}

// Log records one key intermediate state and the reason for the next
// judgment. kv is an even list of extra key/value pairs.
func (l *Logger) Log(state, reason string, kv ...any) {
	l.t.Helper()
	entry := map[string]any{
		"run":    l.Run,
		"test":   l.t.Name(),
		"ts":     time.Now().UTC().Format(time.RFC3339Nano),
		"state":  state,
		"reason": reason,
	}
	for i := 0; i+1 < len(kv); i += 2 {
		if k, ok := kv[i].(string); ok {
			entry[k] = kv[i+1]
		}
	}
	line, err := json.Marshal(entry)
	if err != nil {
		l.t.Fatalf("testlog: marshal failed: %v", err)
	}
	l.mu.Lock()
	l.f.Write(append(line, '\n'))
	l.mu.Unlock()
	l.t.Logf("run=%s state=%s reason=%q kv=%v", l.Run, state, reason, kv)
}
