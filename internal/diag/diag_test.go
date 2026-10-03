package diag_test

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"rlmod/internal/diag"
)

func TestLoggerCorrelationIDs(t *testing.T) {
	var buf bytes.Buffer
	l := diag.NewLogger(&buf, "req-abc", true)
	l.Log("info", diag.Reject, "core.N", "inline const changed", map[string]string{"class": "inline_const"})
	l.Log("info", diag.Accept, "app.Run", "reuse", nil)

	entries := l.Entries()
	if len(entries) != 2 {
		t.Fatalf("entries = %d", len(entries))
	}
	for i, e := range entries {
		if e.Request != "req-abc" {
			t.Fatalf("entry %d request id = %s", i, e.Request)
		}
		if e.Record == "" || !strings.HasPrefix(e.Record, "req-abc-r") {
			t.Fatalf("entry %d record id = %s", i, e.Record)
		}
		if e.Reason == "" || e.Subject == "" {
			t.Fatalf("entry %d missing reason/subject", i)
		}
	}
	if entries[0].Decision != diag.Reject || entries[1].Decision != diag.Accept {
		t.Fatalf("decisions = %s,%s", entries[0].Decision, entries[1].Decision)
	}
	// emitted output must be valid JSON lines with the request id
	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("json lines = %d", len(lines))
	}
	var raw map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &raw); err != nil {
		t.Fatalf("not valid json: %v", err)
	}
	if raw["request_id"] != "req-abc" {
		t.Fatalf("json request_id = %v", raw["request_id"])
	}
}

func TestMaskValueHidesContent(t *testing.T) {
	secret := "SECRET-1234567890"
	masked := diag.MaskValue("str", secret)
	if strings.Contains(masked, secret) {
		t.Fatalf("mask leaked value: %s", masked)
	}
	if !strings.Contains(masked, "redacted") {
		t.Fatalf("mask should mark redaction: %s", masked)
	}
	if strings.Contains(masked, "1234567890") {
		t.Fatal("mask must not contain any raw characters of the secret")
	}
}

func TestGeneratedRequestID(t *testing.T) {
	l := diag.NewLogger(&bytes.Buffer{}, "", false)
	if !strings.HasPrefix(l.RequestID(), "req_") {
		t.Fatalf("auto request id = %s", l.RequestID())
	}
}
