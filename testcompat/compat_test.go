// Package compat_test cross-checks the BER codec against two independent,
// mature ASN.1 implementations: the Go standard library encoding/asn1 and
// github.com/go-asn1-ber/asn1-ber. Reference octets come from those
// libraries, never from the implementation under test.
package compat_test

import (
	"encoding/asn1"
	"encoding/hex"
	"math/big"
	"testing"

	berlib "github.com/go-asn1-ber/asn1-ber"

	"berd/internal/ber"
	"berd/internal/harness"
)

var int64Values = []int64{
	0, 1, -1, 5, 127, 128, 255, 256, -127, -128, -129, -256,
	65535, 65536, 65537, -65537,
	1<<31 - 1, -(1 << 31), 1 << 32, -(1 << 32),
	1<<63 - 1, -(1 << 63),
}

// TestIntegerDERMatchesStdlib: our DER INTEGER encoding must be octet-equal
// to encoding/asn1's output for the same value.
func TestIntegerDERMatchesStdlib(t *testing.T) {
	lim := ber.DefaultLimits()
	for _, v := range int64Values {
		t.Run(hex.EncodeToString(big.NewInt(v).Bytes()), func(t *testing.T) {
			h := harness.New(t)
			ref, err := asn1.Marshal(v)
			if err != nil {
				t.Fatalf("asn1.Marshal(%d): %v", v, err)
			}
			node, berr := ber.Build(ber.Spec{Type: "integer", Value: big.NewInt(v).String()}, lim)
			if berr != nil {
				t.Fatalf("build: %v", berr)
			}
			got, derr := ber.EncodeDER(node, lim)
			if derr != nil {
				t.Fatalf("EncodeDER: %v", derr)
			}
			h.Step("value=%d reference=encoding/asn1", v)
			h.ExpectBytes("DER vs encoding/asn1", got, hex.EncodeToString(ref))
		})
	}
}

// TestBigIntegerDERMatchesStdlib covers integers beyond int64.
func TestBigIntegerDERMatchesStdlib(t *testing.T) {
	lim := ber.DefaultLimits()
	values := []string{
		"18446744073709551616",            // 2^64
		"-18446744073709551616",           // -2^64
		"1267650600228229401496703205376", // 2^100
		"-1267650600228229401496703205375",
	}
	for _, s := range values {
		t.Run(s, func(t *testing.T) {
			h := harness.New(t)
			v, _ := new(big.Int).SetString(s, 10)
			ref, err := asn1.Marshal(v)
			if err != nil {
				t.Fatalf("asn1.Marshal(%s): %v", s, err)
			}
			node, berr := ber.Build(ber.Spec{Type: "integer", Value: s}, lim)
			if berr != nil {
				t.Fatalf("build: %v", berr)
			}
			got, derr := ber.EncodeDER(node, lim)
			if derr != nil {
				t.Fatalf("EncodeDER: %v", derr)
			}
			h.ExpectBytes("DER vs encoding/asn1", got, hex.EncodeToString(ref))

			// And our decoder must recover the value from the stdlib octets.
			root, perr := ber.DecodeAll(ref, lim)
			if perr != nil {
				t.Fatalf("decode stdlib bytes: %v", perr)
			}
			back, verr := root.Integer(lim)
			if verr != nil {
				t.Fatalf("integer: %v", verr)
			}
			h.ExpectEqual("decode of stdlib encoding", back.String(), s)
		})
	}
}

// TestIntegerDecodeMatchesStdlib: encoding/asn1 must decode our DER back to
// the original value.
func TestIntegerDecodeMatchesStdlib(t *testing.T) {
	lim := ber.DefaultLimits()
	for _, v := range int64Values {
		node, _ := ber.Build(ber.Spec{Type: "integer", Value: big.NewInt(v).String()}, lim)
		der, _ := ber.EncodeDER(node, lim)
		var out int64
		rest, err := asn1.Unmarshal(der, &out)
		if err != nil {
			t.Fatalf("asn1.Unmarshal(%x): %v", der, err)
		}
		if len(rest) != 0 {
			t.Fatalf("asn1.Unmarshal left %d trailing bytes", len(rest))
		}
		if out != v {
			t.Fatalf("round trip: got %d, want %d", out, v)
		}
	}
	t.Log("verdict=PASS basis=encoding/asn1 decoded every value identically")
}

// TestSequenceMatchesStdlib: our DER SEQUENCE-of-INTEGERs must match
// encoding/asn1's struct encoding octet for octet.
func TestSequenceMatchesStdlib(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	type pair struct {
		A int64
		B int64
	}
	ref, err := asn1.Marshal(pair{A: 5, B: -129})
	if err != nil {
		t.Fatalf("asn1.Marshal: %v", err)
	}
	node, berr := ber.Build(ber.Spec{Type: "sequence", Children: []ber.Spec{
		{Type: "integer", Value: "5"},
		{Type: "integer", Value: "-129"},
	}}, lim)
	if berr != nil {
		t.Fatalf("build: %v", berr)
	}
	got, derr := ber.EncodeDER(node, lim)
	if derr != nil {
		t.Fatalf("EncodeDER: %v", derr)
	}
	h.ExpectBytes("SEQUENCE DER vs encoding/asn1", got, hex.EncodeToString(ref))

	// Stdlib must decode our SEQUENCE back into the struct.
	var back pair
	if _, err := asn1.Unmarshal(got, &back); err != nil {
		t.Fatalf("asn1.Unmarshal: %v", err)
	}
	h.ExpectEqual("struct round trip", back, pair{A: 5, B: -129})
}

// TestBitStringMatchesStdlib: BIT STRING encodings must match
// encoding/asn1's asn1.BitString output.
func TestBitStringMatchesStdlib(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	cases := []struct {
		bytes     []byte
		bitLength int
	}{
		{[]byte{0x6e, 0x5d}, 16},
		{[]byte{0x6e, 0x58}, 13}, // 3 padding bits, zeroed
		{[]byte{0xe0}, 3},        // 5 padding bits, zeroed
		{[]byte{}, 0},
	}
	for _, tc := range cases {
		ref, err := asn1.Marshal(asn1.BitString{Bytes: tc.bytes, BitLength: tc.bitLength})
		if err != nil {
			t.Fatalf("asn1.Marshal bitstring: %v", err)
		}
		unused := 8*len(tc.bytes) - tc.bitLength
		node, berr := ber.Build(ber.Spec{
			Type: "bitstring", Hex: hex.EncodeToString(tc.bytes), UnusedBits: unused,
		}, lim)
		if berr != nil {
			t.Fatalf("build: %v", berr)
		}
		got, derr := ber.EncodeDER(node, lim)
		if derr != nil {
			t.Fatalf("EncodeDER: %v", derr)
		}
		h.ExpectBytes("BIT STRING DER vs encoding/asn1", got, hex.EncodeToString(ref))
	}
}

// TestGoAsn1BerDecode: the independent go-asn1-ber library must decode our
// encodings to the same values.
func TestGoAsn1BerDecode(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	for _, v := range []int64{0, 5, -129, 65537, 1 << 40} {
		node, _ := ber.Build(ber.Spec{Type: "integer", Value: big.NewInt(v).String()}, lim)
		der, _ := ber.EncodeDER(node, lim)
		pkt := berlib.DecodePacket(der)
		if pkt == nil {
			t.Fatalf("go-asn1-ber failed to decode %x", der)
		}
		got, ok := pkt.Value.(int64)
		if !ok {
			t.Fatalf("go-asn1-ber value type %T for %x", pkt.Value, der)
		}
		h.ExpectEqual("go-asn1-ber decode of our DER", got, v)
	}
}

// TestGoAsn1BerEncode: we must decode go-asn1-ber's encodings to the same
// values, including a SEQUENCE built with the reference library.
func TestGoAsn1BerEncode(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()

	seq := berlib.NewSequence("test")
	seq.AppendChild(berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, int64(5), "a"))
	seq.AppendChild(berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, int64(-129), "b"))
	data := seq.Bytes()
	h.Step("reference go-asn1-ber SEQUENCE bytes=%s", hex.EncodeToString(data))

	root, err := ber.DecodeAll(data, lim)
	if err != nil {
		t.Fatalf("decode go-asn1-ber output: %v", err)
	}
	if verr := ber.Validate(root, lim); verr != nil {
		t.Fatalf("validate: %v", verr)
	}
	h.ExpectEqual("child count", len(root.Children), 2)
	a, _ := root.Children[0].Integer(lim)
	b, _ := root.Children[1].Integer(lim)
	h.ExpectEqual("child 0", a.String(), "5")
	h.ExpectEqual("child 1", b.String(), "-129")
}

// TestTruncationRejectedByBoth: truncated inputs must be rejected by our
// codec (with the hand-computed offset) and independently by go-asn1-ber.
func TestTruncationRejectedByBoth(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()
	cases := []struct {
		hex string
		off int
	}{
		{"02", 1},
		{"0201", 2},
		{"0282ff", 3},
		{"3005020105", 5},
		{"3080020105", 5}, // indefinite missing EOC
	}
	for _, tc := range cases {
		data := harness.MustDecodeHex(t, tc.hex)
		_, ourErr := ber.DecodeAll(data, lim)
		h.ExpectError(data, ourErr, ber.CatTruncation, tc.off)
		if _, refErr := berlib.DecodePacketErr(data); refErr == nil {
			t.Fatalf("go-asn1-ber accepted truncated input %s", tc.hex)
		}
		h.Step("verdict=PASS basis=go-asn1-ber also rejected %s", tc.hex)
	}
}
