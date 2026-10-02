package diag_test

import (
	"strings"
	"testing"

	"coaplab/internal/diag"
)

// Every decision record carries MID/Token identifiers and an explicit
// verdict; rejections carry an assertable failure category.
func TestRecorder_CarriesIdentifiersAndVerdict(t *testing.T) {
	rec := diag.NewRecorder(nil).Quiet()
	rec.Log("127.0.0.1:7", 0x7d34, true, "cafe", diag.Reject, diag.CatGap,
		"block %d offset mismatch", 3)
	ev := rec.Events()
	if len(ev) != 1 {
		t.Fatalf("events=%d", len(ev))
	}
	e := ev[0]
	if e.MID != "0x7d34" || e.Token != "cafe" || e.Remote != "127.0.0.1:7" {
		t.Fatalf("identity fields lost: %+v", e)
	}
	if e.Verdict != diag.Reject || e.Category != diag.CatGap {
		t.Fatalf("verdict/category lost: %+v", e)
	}
	if !strings.Contains(e.Detail, "block 3 offset mismatch") {
		t.Fatalf("detail lost: %q", e.Detail)
	}
	if rec.ByCategory(diag.CatGap)[0].MID != "0x7d34" {
		t.Fatal("category lookup failed")
	}
	if !rec.HasVerdict(diag.Reject) || rec.HasVerdict(diag.Accept) {
		t.Fatal("verdict queries wrong")
	}
}

// Sensitive material passed through Redact is rendered as length only, so
// payload bytes can never appear in a log line.
func TestRedact_NeverPrintsContent(t *testing.T) {
	secret := []byte("AKIA-PASSWORD-TOKEN-1234567890") // 30 bytes
	out := diag.Redact(secret)
	if strings.Contains(out, "PASSWORD") || strings.Contains(out, "AKIA") {
		t.Fatalf("redaction leaked payload: %q", out)
	}
	if !strings.Contains(out, "30 bytes") {
		t.Fatalf("redaction should report length, got %q", out)
	}
	if got := diag.RedactString("token=supersecretvalue&x=1"); got == "token=supersecretvalue&x=1" {
		t.Fatal("long query value was not bounded")
	}
}
