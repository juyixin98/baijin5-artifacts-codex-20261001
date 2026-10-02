package etag_test

import (
	"bytes"
	"strings"
	"testing"

	"coaplab/internal/etag"
)

// Identical (content-format, body) pairs have identical etags; a one-byte
// change yields a different validator (the basis of mid-download version
// rejection).
func TestCompute_DeterministicAndSensitive(t *testing.T) {
	e1 := etag.Compute(0, []byte("abc"))
	e2 := etag.Compute(0, []byte("abc"))
	if !bytes.Equal(e1, e2) {
		t.Fatal("same representation must hash equal")
	}
	if bytes.Equal(e1, etag.Compute(0, []byte("abd"))) {
		t.Fatal("one-byte change must change etag")
	}
	if bytes.Equal(e1, etag.Compute(50, []byte("abc"))) {
		t.Fatal("different content-format must change etag")
	}
	if len(e1) != etag.Len {
		t.Fatalf("etag len %d", len(e1))
	}
}

// TestVectors are stable constants tests can hard-code without calling
// Compute themselves.
func TestVectors_Stable(t *testing.T) {
	if len(etag.TestVectors.HelloText) != 8 {
		t.Fatalf("hello vector length: %d", len(etag.TestVectors.HelloText))
	}
	if !bytes.Equal(etag.TestVectors.HelloText, etag.Compute(0, []byte("Hello, CoAP"))) {
		t.Fatal("hello vector drifted from Compute")
	}
	if !bytes.Equal(etag.TestVectors.Empty, etag.Compute(0, nil)) {
		t.Fatal("empty vector drifted")
	}
}

// Equal treats absent etags as NOT equal (unknown version).
func TestEqual_AbsentNotEqual(t *testing.T) {
	if etag.Equal(nil, []byte{1}) {
		t.Fatal("nil etag must not match a concrete etag")
	}
	if etag.Equal([]byte{}, []byte{}) {
		t.Fatal("empty etags must not be considered equal")
	}
	if !etag.Equal([]byte{1, 2, 3}, []byte{1, 2, 3}) {
		t.Fatal("equal etags must compare equal")
	}
}

// MaskedHex must elide the middle of the value and never emit the full etag.
func TestMaskedHex_ElidesMiddle(t *testing.T) {
	full := etag.Hex(etag.TestVectors.HelloText)
	masked := etag.MaskedHex(etag.TestVectors.HelloText)
	if masked == full {
		t.Fatal("masked etag must differ from full etag")
	}
	if !strings.Contains(masked, "…") {
		t.Fatalf("masked etag must contain elision marker, got %q", masked)
	}
}
