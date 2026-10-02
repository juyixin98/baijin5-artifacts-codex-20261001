package logx

import (
	"bytes"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"berconf/internal/ber"
)

func TestRunIDAndFingerprint(t *testing.T) {
	id1, id2 := NewRunID(), NewRunID()
	if len(id1) != 32 || id1 == id2 || strings.Contains(id1, "-") {
		t.Fatalf("run ids must be 32 hex chars and unique: %q %q", id1, id2)
	}
	fp := InputFingerprint([]byte{0x02, 0x01, 0x01})
	if fp["input_len"].(int) != 3 || fp["input_hex_head"] != "020101" {
		t.Fatalf("fingerprint wrong: %+v", fp)
	}
	if fp["input_sha256"].(string) != "da4d3da1daaceba9b72635459fd6a17da13e7530d53a4932535e6dc1d49bde3d" {
		t.Fatalf("unexpected sha256: %v", fp["input_sha256"])
	}
}

func TestStructuredLinesCarryIdentity(t *testing.T) {
	var buf bytes.Buffer
	l := &Logger{
		out: &buf, level: LevelDebug, jsonFmt: true,
		fields: map[string]any{"service_version": "1.0.0"},
	}
	child := l.With(map[string]any{"run_id": "run-xyz", "input_sha256": "deadbeef"})
	child.Info("decode ok", map[string]any{"bytes": 5})

	line := buf.String()
	if !strings.Contains(line, `"run_id":"run-xyz"`) ||
		!strings.Contains(line, `"input_sha256":"deadbeef"`) ||
		!strings.Contains(line, `"service_version":"1.0.0"`) {
		t.Fatalf("log line missing identity fields: %s", line)
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(line), &parsed); err != nil {
		t.Fatalf("log line is not valid json: %v: %s", err, line)
	}
}

func TestTracerLogsStepsWithOffsetsAndRules(t *testing.T) {
	var buf bytes.Buffer
	l := &Logger{
		out: &buf, level: LevelDebug, jsonFmt: true,
		fields: map[string]any{"service_version": "1.0.0"},
	}
	var (
		mu    sync.Mutex
		seq   int
		steps int
	)
	tracer := l.With(map[string]any{"run_id": "run-tr"}).Tracer("run-tr", &seq, &mu)
	_ = steps
	tracer(ber.Step{Name: "eoc", Offset: 7, Depth: 1, Detail: "matched EOC"})

	line := buf.String()
	var parsed map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(line)), &parsed); err != nil {
		t.Fatalf("invalid json: %v", err)
	}
	if parsed["offset"].(float64) != 7 || parsed["run_id"] != "run-tr" {
		t.Fatalf("tracer line mismatch: %v", parsed)
	}
	if !strings.Contains(parsed["rule"].(string), "X.690") {
		t.Fatalf("tracer must state the rule basis: %v", parsed["rule"])
	}
}

func TestLevelFiltering(t *testing.T) {
	var buf bytes.Buffer
	l := &Logger{out: &buf, level: LevelWarn, jsonFmt: true, fields: map[string]any{}}
	l.Debug("hidden", nil)
	l.Info("hidden", nil)
	l.Warn("shown", nil)
	if strings.Contains(buf.String(), "hidden") || !strings.Contains(buf.String(), "shown") {
		t.Fatalf("level filtering wrong: %q", buf.String())
	}
}
