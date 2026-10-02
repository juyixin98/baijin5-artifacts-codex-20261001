package mblog

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestEventWritesStructuredRecord(t *testing.T) {
	var buf bytes.Buffer
	l := New(&buf, "test")
	l.Event("something", "txid", 5, "reason", "because")

	var rec map[string]any
	if err := json.Unmarshal(buf.Bytes(), &rec); err != nil {
		t.Fatalf("log line is not JSON: %v", err)
	}
	for _, key := range []string{"ts", "run", "comp", "ev", "txid", "reason"} {
		if _, ok := rec[key]; !ok {
			t.Fatalf("record missing key %q: %v", key, rec)
		}
	}
	if rec["comp"] != "test" || rec["ev"] != "something" || rec["reason"] != "because" {
		t.Fatalf("unexpected record: %v", rec)
	}
	if rec["txid"].(float64) != 5 {
		t.Fatalf("txid not preserved: %v", rec["txid"])
	}
}

func TestFrameFingerprintDeterministic(t *testing.T) {
	frame := []byte{0, 1, 0, 0, 0, 6, 1, 3, 0, 0, 0, 2}
	a, b := FrameFingerprint(frame), FrameFingerprint(frame)
	if a != b || len(a) != 12 {
		t.Fatalf("fingerprint unstable or wrong length: %q", a)
	}
	if FrameFingerprint([]byte{1}) == FrameFingerprint([]byte{2}) {
		t.Fatal("different frames share a fingerprint")
	}
}
