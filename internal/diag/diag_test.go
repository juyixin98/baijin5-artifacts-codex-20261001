package diag

import (
	"bytes"
	"strings"
	"testing"
)

func TestDiagnosticsCarryIdentityAndState(t *testing.T) {
	var buf bytes.Buffer
	log := NewLogger(&buf, "req-fixed-1", "billing")
	log.Reject(CatFingerprint, "billing.total", "interface changed",
		map[string]any{"old": "aaaa", "new": "bbbb", "rebuild": true})
	out := buf.String()
	for _, want := range []string{"rejected", "req=req-fixed-1", "mod=billing", "sym=billing.total",
		"cat=fingerprint_changed", "old=aaaa", "rebuild=true"} {
		if !strings.Contains(out, want) {
			t.Fatalf("diagnostic %q missing %q", out, want)
		}
	}
}

func TestAcceptRejectUndecidable(t *testing.T) {
	var buf bytes.Buffer
	log := NewLogger(&buf, "req-x", "")
	log.Accept("s", "ok", nil)
	log.Reject(CatType, "s", "bad", nil)
	log.Undecidable(CatCacheCorrupt, "s", "unknown", nil)
	out := buf.String()
	if strings.Count(out, "accepted") != 1 || strings.Count(out, "rejected") != 1 ||
		strings.Count(out, "undecidable") != 1 {
		t.Fatalf("verdicts wrong: %s", out)
	}
	if !strings.Contains(out, "cat=type_error") || !strings.Contains(out, "cat=cache_corrupt") {
		t.Fatalf("categories missing: %s", out)
	}
}

func TestRequestIDStable(t *testing.T) {
	log := NewLogger(nil, "", "")
	id := log.RequestID()
	if !strings.HasPrefix(id, "req-") || len(id) != len("req-")+16 {
		t.Fatalf("bad request id %q", id)
	}
	child := log.WithModule("m")
	if child.RequestID() != id {
		t.Fatal("child logger must inherit request id")
	}
}

func TestRedact(t *testing.T) {
	if r := Redact(""); !strings.Contains(r, "empty") {
		t.Fatalf("empty redaction: %q", r)
	}
	r := Redact("SENSITIVE-RECORD")
	if strings.ContainsAny(r, "ENSITIVE") && strings.Contains(r, "RECORD") {
		t.Fatalf("redaction leaks: %q", r)
	}
	if !strings.Contains(r, "len=16") {
		t.Fatalf("want length marker, got %q", r)
	}
}
