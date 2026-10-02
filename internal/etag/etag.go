// Package etag provides deterministic ETags for resource versions.
//
// RFC 7252 §5.10.6 / RFC 7959 §2.4: the ETag Option(4) is the validator
// binding every block of one representation. The etag here is SHA-256
// over (content-format prefix + representation bytes), truncated to 8
// bytes — identical bytes imply an identical ETag; any change yields a
// new one.
package etag

import (
	"crypto/sha256"
	"encoding/hex"
)

// Len is the ETag length in wire bytes. 8 bytes is within the
// RFC 7252 §5.10.7 opaque validator range (0..8 bytes in requests).
const Len = 8

// Compute returns the 8-byte ETag for a concrete representation.
// contentFormat is included so a resource available in two formats gets
// distinct validators.
func Compute(contentFormat uint16, body []byte) []byte {
	h := sha256.New()
	var cf [2]byte
	cf[0] = byte(contentFormat >> 8)
	cf[1] = byte(contentFormat)
	h.Write(cf[:])
	h.Write(body)
	sum := h.Sum(nil)
	return sum[:Len]
}

// Equal compares two etags; nil/empty means "no validator present" and is
// never considered equal to a concrete etag.
func Equal(a, b []byte) bool {
	if len(a) == 0 || len(b) == 0 || len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// TestVectors are fixed ETags for compatibility tests: tests can hard-code
// the expected bytes instead of reimplementing Compute() themselves.
var TestVectors = struct {
	Empty     []byte
	HelloText []byte
}{
	Empty:     Compute(0, nil),
	HelloText: Compute(0, []byte("Hello, CoAP")),
}

// Hex renders an etag as 0x….
func Hex(e []byte) string { return "0x" + hex.EncodeToString(e) }

// HexShort renders an etag without the 0x prefix, "-" when absent; used in
// terse diagnostic detail strings.
func HexShort(e []byte) string {
	if len(e) == 0 {
		return "-"
	}
	return hex.EncodeToString(e)
}

// MaskedHex renders an etag for logs with its middle elided. ETags are not
// secret, but all diagnostics that print identifying bytes go through the
// same masking path so secret-bearing values are masked the same way.
func MaskedHex(e []byte) string {
	s := Hex(e)
	if len(s) <= 6 {
		return s
	}
	return s[:5] + "…" + s[len(s)-3:]
}
