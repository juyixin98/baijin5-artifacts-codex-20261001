// Package harness provides correlated, evidence-style test logging: every
// assertion records the run identity, the exact input, the expectation, the
// observed result and the verdict basis.
package harness

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"runtime"
	"testing"

	"berd/internal/ber"
	"berd/internal/version"
)

// Logger correlates all log lines of one test with a run ID.
type Logger struct {
	t     *testing.T
	RunID string
	step  int
}

// New creates a Logger and logs the test identity header.
func New(t *testing.T) *Logger {
	t.Helper()
	var b [4]byte
	_, _ = rand.Read(b[:])
	l := &Logger{t: t, RunID: fmt.Sprintf("test-%x", b[:])}
	t.Logf("run_id=%s test=%s service_version=%s go_version=%s",
		l.RunID, t.Name(), version.Version, runtime.Version())
	return l
}

// Step logs one computation step with its evidence.
func (l *Logger) Step(format string, args ...any) {
	l.t.Helper()
	l.step++
	l.t.Logf("run_id=%s step=%d %s", l.RunID, l.step, fmt.Sprintf(format, args...))
}

// ExpectError asserts err carries the given category and offset, logging the
// verdict and its basis.
func (l *Logger) ExpectError(input []byte, err *ber.Error, cat ber.Category, off int) {
	l.t.Helper()
	l.Step("input_hex=%s expect category=%s offset=%d", hex.EncodeToString(input), cat, off)
	if err == nil {
		l.t.Fatalf("run_id=%s verdict=FAIL: expected %s at offset %d, got success",
			l.RunID, cat, off)
	}
	if err.Category != cat || err.Offset != off {
		l.t.Fatalf("run_id=%s verdict=FAIL: expected %s@%d, got %s@%d (%s)",
			l.RunID, cat, off, err.Category, err.Offset, err.Msg)
	}
	l.Step("verdict=PASS basis=category+offset match got=%s@%d msg=%q",
		err.Category, err.Offset, err.Msg)
}

// ExpectBytes asserts got encodes to wantHex.
func (l *Logger) ExpectBytes(label string, got []byte, wantHex string) {
	l.t.Helper()
	gotHex := hex.EncodeToString(got)
	l.Step("%s got=%s want=%s", label, gotHex, wantHex)
	if gotHex != wantHex {
		l.t.Fatalf("run_id=%s verdict=FAIL: %s: got %s, want %s",
			l.RunID, label, gotHex, wantHex)
	}
	l.Step("verdict=PASS basis=octet-exact match (%s)", label)
}

// ExpectEqual asserts got == want, logging both.
func (l *Logger) ExpectEqual(label string, got, want any) {
	l.t.Helper()
	l.Step("%s got=%v want=%v", label, got, want)
	if fmt.Sprintf("%v", got) != fmt.Sprintf("%v", want) {
		l.t.Fatalf("run_id=%s verdict=FAIL: %s: got %v, want %v",
			l.RunID, label, got, want)
	}
	l.Step("verdict=PASS basis=equality (%s)", label)
}

// MustDecodeHex decodes a hex fixture or fails the test.
func MustDecodeHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("bad fixture hex %q: %v", s, err)
	}
	return b
}
