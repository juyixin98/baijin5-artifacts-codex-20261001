package ber

import (
	"bytes"
	"encoding/hex"
	"errors"
	"math/big"
	"testing"
)

// FuzzDecoderInvariants feeds arbitrary bytes to the decoder and asserts
// safety invariants that must hold for EVERY input:
//   - no panic;
//   - on error: a *DecodeError whose offset is within [0, len(input)];
//   - on success: the node spans the whole buffer (single-value rule),
//     and BER re-encoding then re-decoding yields an identical value tree.
func FuzzDecoderInvariants(f *testing.F) {
	seed := []string{
		"", "00", "020100", "3006020101020102",
		"a080308002010500000000", "300400000200",
		"0280", "02ff00", "03020381", "1f800100",
	}
	for _, s := range seed {
		b, _ := hex.DecodeString(s)
		f.Add(b)
	}

	lim := DefaultLimits()
	f.Fuzz(func(t *testing.T, data []byte) {
		node, err := Decode(data, lim)
		if err != nil {
			var de *DecodeError
			if !errors.As(err, &de) {
				t.Fatalf("non-DecodeError: %T", err)
			}
			if de.Offset < 0 || de.Offset > int64(len(data)) {
				t.Fatalf("offset %d outside [0,%d]", de.Offset, len(data))
			}
			return
		}
		if node.End != int64(len(data)) || node.Start != 0 {
			t.Fatalf("accepted node span [%d,%d] != buffer %d", node.Start, node.End, len(data))
		}
		// Content validation may reject structurally well-formed bytes
		// (e.g. empty INTEGER); that is an expected split, not an
		// invariant violation. Round-trip only profile-valid trees.
		if verr := node.ValidateBER(lim); verr != nil {
			return
		}

		// Round-trip via definite BER encoding.
		out, encErr := EncodeChecked(node, BER)
		if encErr != nil {
			t.Fatalf("re-encode accepted tree: %v", encErr)
		}
		node2, err2 := Decode(out, lim)
		if err2 != nil {
			t.Fatalf("decode re-encoded bytes: %v", err2)
		}
		if !treesEquivalent(node, node2) {
			t.Fatalf("round-trip mismatch:\n in=%x\nout=%x", data, out)
		}
	})
}

// FuzzIndefiniteRoundTrip checks that every BER-indefinite encoding the
// encoder produces decodes cleanly, and DER validation rejects it.
func FuzzIndefiniteRoundTrip(f *testing.F) {
	f.Add(int64(0))
	f.Fuzz(func(t *testing.T, v int64) {
		n := SequenceNode(IntegerNode(v))
		wire := Encode(n, BERIndefinite)
		if got, err := Decode(wire, DefaultLimits()); err != nil {
			t.Fatalf("indefinite decode: %v", err)
		} else if len(got.Children) != 1 {
			t.Fatalf("children = %d", len(got.Children))
		}
		if _, err := DecodeAndValidate(wire, "DER", DefaultLimits()); err == nil {
			t.Fatal("indefinite encoding passed DER")
		}
	})
}

// FuzzBigIntegerRoundTrip covers arbitrary-precision signed INTEGER
// encoding/decoding against math/big.
func FuzzBigIntegerRoundTrip(f *testing.F) {
	f.Add(byte(0), []byte{0})
	f.Fuzz(func(t *testing.T, sign byte, mag []byte) {
		if len(mag) > 200 {
			mag = mag[:200]
		}
		v := new(big.Int).SetBytes(mag)
		if sign&1 != 0 {
			v.Neg(v)
		}
		lim := DefaultLimits()
		lim.MaxIntegerBytes = 256
		node := BigIntegerNode(v)
		if err := node.ValidateBER(lim); err != nil {
			t.Skip("width beyond fuzz profile")
		}
		wire := Encode(node, DER)
		decoded, err := DecodeAndValidate(wire, "DER", lim)
		if err != nil {
			t.Fatalf("DER decode: %v", err)
		}
		got, err := BigIntegerValue(decoded)
		if err != nil {
			t.Fatal(err)
		}
		if got.Cmp(v) != 0 {
			t.Fatalf("big int round-trip: got %s want %s", got, v)
		}
	})
}

func treesEquivalent(a, b *Node) bool {
	if a.Class != b.Class || a.Tag != b.Tag || a.Constructed != b.Constructed {
		return false
	}
	if !bytes.Equal(a.Value, b.Value) {
		return false
	}
	if len(a.Children) != len(b.Children) {
		return false
	}
	for i := range a.Children {
		if !treesEquivalent(a.Children[i], b.Children[i]) {
			return false
		}
	}
	return true
}
