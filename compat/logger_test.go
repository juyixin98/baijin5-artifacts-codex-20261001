package compat_test

import (
	"io"
	"log/slog"
	"testing"
)

// testLogger sends server logs to the test output only on -v/failure.
func testLogger(t *testing.T) *slog.Logger {
	return slog.New(slog.NewTextHandler(
		&testWriter{t: t},
		&slog.HandlerOptions{Level: slog.LevelWarn}))
}

type testWriter struct{ t *testing.T }

func (w *testWriter) Write(p []byte) (int, error) {
	w.t.Logf("%s", p)
	return len(p), nil
}

var _ io.Writer = (*testWriter)(nil)
