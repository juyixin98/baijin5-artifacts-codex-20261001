package server_test

import (
	"io"
	"log/slog"
	"testing"
)

// testLogger keeps server logs attached to the failing test's output and
// discards them on success, instead of spamming the console.
func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

var _ = testing.Verbose
