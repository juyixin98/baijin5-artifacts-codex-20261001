package ber_test

import (
	"testing"

	"berd/internal/ber"
	"berd/internal/harness"
)

// TestBuildAndEncodeDER builds values from JSON specs and checks the exact
// DER octets against hand-computed expectations.
func TestBuildAndEncodeDER(t *testing.T) {
	lim := ber.DefaultLimits()
	cases := []struct {
		name string
		spec ber.Spec
		want string
	}{
		{"int-5", ber.Spec{Type: "integer", Value: "5"}, "020105"},
		{"int-neg129", ber.Spec{Type: "integer", Value: "-129"}, "0202ff7f"},
		{"int-big", ber.Spec{Type: "integer", Value: "18446744073709551616"}, "0209010000000000000000"},
		{"bitstring", ber.Spec{Type: "bitstring", Hex: "6e5d"}, "0303006e5d"},
		{"bitstring-padded", ber.Spec{Type: "bitstring", Hex: "e0", UnusedBits: 5}, "030205e0"},
		{"seq", ber.Spec{Type: "sequence", Children: []ber.Spec{
			{Type: "integer", Value: "5"},
			{Type: "integer", Value: "6"},
		}}, "3006020105020106"},
		{"context-primitive", ber.Spec{Type: "context", Tag: 3, Hex: "0101"}, "83020101"},
		{"context-constructed", ber.Spec{Type: "context", Tag: 0, Constructed: true,
			Children: []ber.Spec{{Type: "integer", Value: "5"}}}, "a003020105"},
		{"context-long-tag", ber.Spec{Type: "context", Tag: 31, Hex: "01"}, "9f1f0101"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := harness.New(t)
			node, err := ber.Build(tc.spec, lim)
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			der, derr := ber.EncodeDER(node, lim)
			if derr != nil {
				t.Fatalf("EncodeDER: %v", derr)
			}
			h.ExpectBytes("DER", der, tc.want)

			// Round-trip: decoding the output must reproduce the value.
			back, perr := ber.DecodeAll(der, lim)
			if perr != nil {
				t.Fatalf("re-decode: %v", perr)
			}
			if verr := ber.Validate(back, lim); verr != nil {
				t.Fatalf("re-validate: %v", verr)
			}
			der2, derr := ber.EncodeDER(back, lim)
			if derr != nil {
				t.Fatalf("re-EncodeDER: %v", derr)
			}
			h.ExpectBytes("DER round-trip stable", der2, tc.want)
		})
	}
}

// TestBuildRejectsBadSpecs checks spec-level validation errors.
func TestBuildRejectsBadSpecs(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()

	_, err := ber.Build(ber.Spec{Type: "integer", Value: "not-a-number"}, lim)
	if err == nil || err.Category != ber.CatSyntax {
		t.Fatalf("expected syntax error, got %v", err)
	}
	h.Step("verdict=PASS basis=bad integer rejected with category=%s", err.Category)

	_, err = ber.Build(ber.Spec{Type: "bitstring", Hex: "", UnusedBits: 3}, lim)
	if err == nil || err.Category != ber.CatSyntax {
		t.Fatalf("expected syntax error, got %v", err)
	}
	h.Step("verdict=PASS basis=empty bitstring with unused bits rejected")

	_, err = ber.Build(ber.Spec{Type: "bitstring", Hex: "ff", UnusedBits: 9}, lim)
	if err == nil || err.Category != ber.CatSyntax {
		t.Fatalf("expected syntax error, got %v", err)
	}
	h.Step("verdict=PASS basis=unused-bits >7 rejected")
}

// TestEncodeBERNormalizesIndefinite checks BER re-encoding flattens
// indefinite-length inputs to definite lengths.
func TestEncodeBERNormalizesIndefinite(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	root := mustRoot(t, harness.MustDecodeHex(t, "3080308002010500000000"), lim)
	out := ber.EncodeBER(root)
	h.ExpectBytes("BER definite re-encode", out, "30053003020105")
}
