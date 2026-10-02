package ber_test

import (
	"testing"

	"berd/internal/ber"
	"berd/internal/harness"
)

// TestIntegerValues checks two's-complement decoding against hand-computed
// values, including 64-bit boundaries.
func TestIntegerValues(t *testing.T) {
	lim := ber.DefaultLimits()
	cases := []struct {
		hex  string
		want string
	}{
		{"020100", "0"},
		{"020101", "1"},
		{"0201ff", "-1"},
		{"02017f", "127"},
		{"02020080", "128"},
		{"020180", "-128"},
		{"0202ff7f", "-129"},
		{"0202ff00", "-256"},
		{"0203010000", "65536"},
		{"0208ffffffffffffffff", "-1"},
		{"02087fffffffffffffff", "9223372036854775807"},
		{"02088000000000000000", "-9223372036854775808"},
		{"0209010000000000000000", "18446744073709551616"},
	}
	for _, tc := range cases {
		t.Run(tc.hex, func(t *testing.T) {
			h := harness.New(t)
			root := mustRoot(t, harness.MustDecodeHex(t, tc.hex), lim)
			v, err := root.Integer(lim)
			if err != nil {
				t.Fatalf("integer: %v", err)
			}
			h.ExpectEqual("value of "+tc.hex, v.String(), tc.want)
		})
	}
}

// TestIntegerMinimality checks the DER minimality predicate directly.
func TestIntegerMinimality(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	cases := []struct {
		hex     string
		minimal bool
	}{
		{"02017f", true},
		{"02020080", true},  // 128 needs the leading zero
		{"0202007f", false}, // redundant leading zero
		{"0201ff", true},    // -1
		{"0202ff80", false}, // redundant leading 0xff
		{"0202ff7f", true},  // -129 needs the leading 0xff
	}
	for _, tc := range cases {
		root := mustRoot(t, harness.MustDecodeHex(t, tc.hex), lim)
		h.ExpectEqual("minimal "+tc.hex, root.IntegerMinimal(), tc.minimal)
	}
}

// TestBitStringUnusedBits pins the unused-bits validation rules.
func TestBitStringUnusedBits(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()

	// unused=7 with one payload octet is legal BER.
	root := mustRoot(t, harness.MustDecodeHex(t, "03020780"), lim)
	bs, err := root.BitString(lim)
	if err != nil {
		t.Fatalf("bitstring: %v", err)
	}
	h.ExpectEqual("unused", bs.Unused, 7)
	h.ExpectEqual("bit length", bs.BitLength(), 1)
	h.ExpectEqual("padding zero", bs.UnusedBitsZero(), true)

	// Same payload with the padding bit set: legal BER, not DER-clean.
	root = mustRoot(t, harness.MustDecodeHex(t, "03020781"), lim)
	bs, err = root.BitString(lim)
	if err != nil {
		t.Fatalf("bitstring: %v", err)
	}
	h.ExpectEqual("padding zero", bs.UnusedBitsZero(), false)

	// unused=8 is out of range: syntax at the unused-bits octet (offset 2).
	bad := harness.MustDecodeHex(t, "030108")
	r := mustRoot(t, bad, lim)
	_, berr := r.BitString(lim)
	h.ExpectError(bad, berr, ber.CatSyntax, 2)
}
