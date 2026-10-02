package logx

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseLevelAllForms(t *testing.T) {
	cases := []struct {
		in   string
		want Level
		ok   bool
	}{
		{"", LevelDebug, true},
		{"debug", LevelDebug, true},
		{"INFO", LevelInfo, true},
		{" warning ", LevelWarn, true},
		{"error", LevelError, true},
		{"trace", LevelInfo, false},
	}
	for _, tc := range cases {
		got, err := ParseLevel(tc.in)
		if tc.ok && err != nil {
			t.Errorf("ParseLevel(%q) unexpected error: %v", tc.in, err)
		}
		if !tc.ok && err == nil {
			t.Errorf("ParseLevel(%q) expected error", tc.in)
		}
		if tc.ok && got != tc.want {
			t.Errorf("ParseLevel(%q) = %v, want %v", tc.in, got, tc.want)
		}
	}
}

func TestFileOutput(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nested", "svc.log")
	l, err := New(path, "json", LevelInfo, "v")
	if err != nil {
		t.Fatalf("file logger: %v", err)
	}
	l.Info("persisted", map[string]any{"k": "v"})
	// close any file by dropping the logger
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read log file: %v", err)
	}
	if !strings.Contains(string(raw), `"msg":"persisted"`) ||
		!strings.Contains(string(raw), `"k":"v"`) {
		t.Fatalf("file content wrong: %s", raw)
	}
}

func TestTextFormat(t *testing.T) {
	var sb strings.Builder
	l := &Logger{out: &sb, level: LevelInfo, jsonFmt: false,
		fields: map[string]any{"service_version": "v"}}
	l.With(map[string]any{"run_id": "r1"}).Warn("careful", map[string]any{"offset": 3})
	line := sb.String()
	if strings.Contains(line, "{") || !strings.Contains(line, "WARN") ||
		!strings.Contains(line, "run=r1") || !strings.Contains(line, "offset=3") {
		t.Fatalf("text log line wrong: %q", line)
	}
}

func TestNewRejectsBadPath(t *testing.T) {
	// A path component that is an existing regular file cannot be a directory.
	blocker := filepath.Join(t.TempDir(), "afile")
	if err := os.WriteFile(blocker, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := New(filepath.Join(blocker, "x.log"), "json", LevelInfo, "v"); err == nil {
		t.Fatal("expected error opening a log path under a regular file")
	}
}
