package ber_test

import (
	"testing"

	"berd/internal/ber"
	"berd/internal/harness"
)

// TestDERCanonicalOutput verifies DER encoding produces the hand-computed
// canonical octets, independent of the input's BER length form.
func TestDERCanonicalOutput(t *testing.T) {
	lim := ber.DefaultLimits()
	cases := []struct {
		name string
		in   string // BER input
		want string // canonical DER output
	}{
		{"int-127", "02017f", "02017f"},
		{"int-128", "02020080", "02020080"},
		{"int-nonminimal-content", "0202007f", "02017f"},
		{"int-neg-nonminimal", "0202ffff", "0201ff"},
		{"long-form-length", "02810105", "020105"},
		{"indefinite-seq", "30800201050000", "3003020105"},
		{"nested-indefinite", "3080308002010500000000", "30053003020105"},
		{"bitstring-pad-zeroed", "030205f9", "030205e0"},
		{"seq-two-ints", "3006020105020106", "3006020105020106"},
		{"context-constructed", "a003020105", "a003020105"},
		{"long-tag", "9f81000101", "9f81000101"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := harness.New(t)
			root := mustRoot(t, harness.MustDecodeHex(t, tc.in), lim)
			der, err := ber.EncodeDER(root, lim)
			if err != nil {
				t.Fatalf("EncodeDER: %v", err)
			}
			h.ExpectBytes("DER of "+tc.in, der, tc.want)
			// Canonical output must verify as DER.
			if verr := ber.VerifyDER(der, lim); verr != nil {
				t.Fatalf("own DER output failed VerifyDER: %v", verr)
			}
		})
	}
}

// TestVerifyDERRejections checks that non-canonical inputs are rejected with
// category constraint at the first differing octet.
func TestVerifyDERRejections(t *testing.T) {
	lim := ber.DefaultLimits()
	cases := []struct {
		name string
		hex  string
		cat  ber.Category
		off  int
	}{
		{"nonminimal-integer", "0202007f", ber.CatConstraint, 1},
		{"nonminimal-length", "02810105", ber.CatConstraint, 1},
		{"indefinite-length", "30800201050000", ber.CatConstraint, 1},
		{"bitstring-nonzero-padding", "030205f9", ber.CatConstraint, 3},
		{"constructed-bitstring", "2304030200ff", ber.CatConstraint, 0},
		{"primitive-sequence", "1000", ber.CatConstraint, 0},
		{"integer-empty", "0200", ber.CatSyntax, 2},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := harness.New(t)
			data := harness.MustDecodeHex(t, tc.hex)
			err := ber.VerifyDER(data, lim)
			h.ExpectError(data, err, tc.cat, tc.off)
		})
	}
}

// TestVerifyDERAccepts confirms canonical inputs pass.
func TestVerifyDERAccepts(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	for _, hexIn := range []string{
		"020100", "02017f", "02020080", "0202ff7f",
		"030100", "0303006e5d", "030205e0",
		"3000", "3006020105020106", "9f1f0101",
	} {
		data := harness.MustDecodeHex(t, hexIn)
		if err := ber.VerifyDER(data, lim); err != nil {
			t.Fatalf("VerifyDER(%s): %v", hexIn, err)
		}
		h.Step("input=%s verdict=PASS basis=canonical re-encode identical", hexIn)
	}
}
