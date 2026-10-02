package ber

import (
	"encoding/hex"
	"errors"
	"math/big"
	"testing"
)

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("bad hex %q: %v", s, err)
	}
	return b
}

// assertDecodeError asserts the exact failure category and byte offset.
func assertDecodeError(t *testing.T, err error, wantKind ErrorKind, wantOffset int64) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected error %s at %d, got success", wantKind, wantOffset)
	}
	var de *DecodeError
	if !errors.As(err, &de) {
		t.Fatalf("expected *DecodeError, got %T: %v", err, err)
	}
	if de.Kind != wantKind {
		t.Fatalf("error kind = %s, want %s (msg: %s)", de.Kind, wantKind, de.Msg)
	}
	if de.Offset != wantOffset {
		t.Fatalf("error offset = %d, want %d (kind %s: %s)", de.Offset, wantOffset, wantKind, de.Msg)
	}
}

func TestDecodeIntegerValues(t *testing.T) {
	cases := []struct {
		name string
		hex  string
		want int64
	}{
		{"zero", "020100", 0},
		{"one", "020101", 1},
		{"127", "02017f", 127},
		{"128", "02020080", 128},
		{"255", "020200ff", 255},
		{"256", "02020100", 256},
		{"neg1", "0201ff", -1},
		{"neg128", "020180", -128},
		{"neg129", "0202ff7f", -129},
		{"neg256", "0202ff00", -256},
		{"maxint64", "02087fffffffffffffff", 9223372036854775807},
		{"minint64", "02088000000000000000", -9223372036854775808}}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			n, err := DecodeAndValidate(mustHex(t, tc.hex), "BER", DefaultLimits())
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			got, overflow, err := IntegerValue(n)
			if err != nil || overflow {
				t.Fatalf("IntegerValue: val=%d overflow=%v err=%v", got, overflow, err)
			}
			if got != tc.want {
				t.Fatalf("integer = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestDecodeBigIntegerRoundTrip(t *testing.T) {
	cases := []*big.Int{
		new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 2400), big.NewInt(7)),
		new(big.Int).Neg(new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 2400), big.NewInt(7))),
		big.NewInt(0), big.NewInt(-1),
	}
	for i, v := range cases {
		n := BigIntegerNode(v)
		wire := Encode(n, DER)
		out, err := DecodeAndValidate(wire, "DER", DefaultLimits())
		if err != nil {
			t.Fatalf("case %d DER decode: %v", i, err)
		}
		got, err := BigIntegerValue(out)
		if err != nil {
			t.Fatalf("case %d: %v", i, err)
		}
		if got.Cmp(v) != 0 {
			t.Fatalf("case %d: got %s, want %s", i, got.String(), v.String())
		}
	}
}

func TestIntegerWidthBound(t *testing.T) {
	// A 300-byte integer is fine under default (4096) ...
	big := make([]byte, 301) // leading 0x00 + 300 magnitude octets
	big[0] = 0x00
	for i := 1; i < len(big); i++ {
		big[i] = 0xFF
	}
	n := &Node{Class: ClassUniversal, Tag: TagInteger, Value: big}
	if err := n.ValidateBER(DefaultLimits()); err != nil {
		t.Fatalf("301-byte integer should pass default limits: %v", err)
	}
	// ... but rejected by a tight 256-byte profile.
	tight := DefaultLimits()
	tight.MaxIntegerBytes = 256
	err := n.ValidateBER(tight)
	assertDecodeError(t, err, KindSizeExceeded, 0)
}

func TestEmptyAndNonminimalInteger(t *testing.T) {
	_, err := DecodeAndValidate(mustHex(t, "0200"), "BER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidInteger, 2)

	// non-minimal INTEGER is legal BER, rejected only by DER
	if _, err := DecodeAndValidate(mustHex(t, "02020000"), "BER", DefaultLimits()); err != nil {
		t.Fatalf("02 02 0000 should be valid BER: %v", err)
	}
	_, err = DecodeAndValidate(mustHex(t, "02020000"), "DER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidEncoding, 2)

	// negative non-minimal form 02 02 FFFF
	_, err = DecodeAndValidate(mustHex(t, "0202ffff"), "DER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidEncoding, 2)
}

func TestBitStringUnusedBits(t *testing.T) {
	ok := []string{"030200a1", "03020380", "030100", "0303040ff0", "03020700"}
	for _, h := range ok {
		if _, err := DecodeAndValidate(mustHex(t, h), "BER", DefaultLimits()); err != nil {
			t.Errorf("%s expected valid: %v", h, err)
		}
	}
	bad := []struct {
		hex    string
		offset int64
	}{
		{"03020381", 3}, // unused=3 but trailing low bits set
		{"03020800", 2}, // unused=8 illegal
		{"030101", 2},   // empty payload with nonzero unused
		{"0300", 2},     // no unused-bits octet
		{"03020701", 3}, // unused=7 with a low bit set
	}
	for _, tc := range bad {
		t.Run(tc.hex, func(t *testing.T) {
			_, err := DecodeAndValidate(mustHex(t, tc.hex), "BER", DefaultLimits())
			assertDecodeError(t, err, KindInvalidBitString, tc.offset)
		})
	}
}

func TestNestedIndefiniteLength(t *testing.T) {
	// A0 80 30 80 02 01 05 00 00 00 00
	in := mustHex(t, "a080308002010500000000")
	n, err := DecodeAndValidate(in, "BER", DefaultLimits())
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if n.Class != ClassContext || n.Tag != 0 || !n.Constructed || !n.Indefinite {
		t.Fatalf("top node mismatch: %+v", n)
	}
	if len(n.Children) != 1 {
		t.Fatalf("want 1 child (SEQUENCE), got %d", len(n.Children))
	}
	seq := n.Children[0]
	if !seq.IsUniversal(TagSequence) || !seq.Indefinite {
		t.Fatalf("child not indefinite SEQUENCE: %+v", seq)
	}
	v, _, _ := IntegerValue(seq.Children[0])
	if v != 5 {
		t.Fatalf("inner integer = %d, want 5", v)
	}
	if n.End != int64(len(in)) {
		t.Fatalf("end = %d, want %d", n.End, len(in))
	}
}

func TestEOCTerminatesOnlyMatchingConstruction(t *testing.T) {
	cases := []struct {
		name   string
		hex    string
		offset int64
	}{
		{"eoc inside definite SEQUENCE", "300400000200", 2},
		{"top-level EOC", "0000", 0},
		{"extra EOC after close", "a08002010100000000", 7},
		{"lone zero at top level", "00", 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := Decode(mustHex(t, tc.hex), DefaultLimits())
			assertDecodeError(t, err, KindMalformedEOC, tc.offset)
		})
	}
}

func TestIndefiniteRequiresEOC(t *testing.T) {
	_, err := Decode(mustHex(t, "a080020101"), DefaultLimits())
	assertDecodeError(t, err, KindTruncated, 5)

	// half EOC at the end
	_, err = Decode(mustHex(t, "a08002010100"), DefaultLimits())
	assertDecodeError(t, err, KindTruncated, 6)

	// indefinite length is illegal on a primitive
	_, err = Decode(mustHex(t, "0280"), DefaultLimits())
	assertDecodeError(t, err, KindInvalidLength, 1)
}

func TestTruncationOffsets(t *testing.T) {
	cases := []struct {
		hex    string
		offset int64
	}{
		{"02", 1},         // missing length octet
		{"020301", 3},     // content short by 2
		{"0282", 2},       // long-form length octets missing
		{"028200", 3},     // one of two length octets missing
		{"3005020101", 5}, // constructed content truncated
		{"1f", 1},         // long-form tag truncated
		{"1f81", 2},       // long-form tag continuation truncated
	}
	for _, tc := range cases {
		t.Run(tc.hex, func(t *testing.T) {
			_, err := Decode(mustHex(t, tc.hex), DefaultLimits())
			assertDecodeError(t, err, KindTruncated, tc.offset)
		})
	}
}

func TestReservedAndIllegalLength(t *testing.T) {
	_, err := Decode(mustHex(t, "02ff00"), DefaultLimits())
	assertDecodeError(t, err, KindInvalidLength, 1)
}

func TestTrailingData(t *testing.T) {
	_, err := Decode(mustHex(t, "02010142"), DefaultLimits())
	assertDecodeError(t, err, KindTrailingData, 3)
}

func TestLongTagAndLengthBounds(t *testing.T) {
	// tag number fitting MaxTagBytes=4 (3 continuation octets, value 0x1FFFFF)
	good := "1fffff7f" + "00" // tag only, then length 0 -> empty primitive
	if _, err := Decode(mustHex(t, good), DefaultLimits()); err != nil {
		t.Fatalf("max-sized tag should decode: %v", err)
	}
	// tag needing 4 continuation octets exceeds MaxTagBytes
	_, err := Decode(mustHex(t, "1f81808000"+"00"), DefaultLimits())
	assertDecodeError(t, err, KindSizeExceeded, 4)

	// length-of-length beyond bound: 6 length octets with MaxLengthBytes=5
	lim := DefaultLimits()
	_, err = Decode(mustHex(t, "0286000000000001"), lim)
	assertDecodeError(t, err, KindLengthOverflow, 1)

	// declared content 65537 exceeds MaxContentBytes (65536)
	_, err = Decode(mustHex(t, "0283010001"), lim)
	assertDecodeError(t, err, KindSizeExceeded, 1)
}

func TestDepthBound(t *testing.T) {
	lim := DefaultLimits()
	lim.MaxDepth = 4
	// 5 nested definite SEQUENCEs around INTEGER 0:
	// 30 0b [30 09 [30 07 [30 05 [30 03 [02 01 00]]]]]
	explicit := mustHex(t, "300b3009300730053003020100")
	_, err := Decode(explicit, lim)
	assertDecodeError(t, err, KindDepthExceeded, 8)

	// depth 4 is the boundary and must pass
	ok := mustHex(t, "3009300730053003020100")
	if _, err := Decode(ok, lim); err != nil {
		t.Fatalf("4 nested SEQUENCEs should pass MaxDepth=4: %v", err)
	}
}

func TestContextTags(t *testing.T) {
	n, err := DecodeAndValidate(mustHex(t, "a203020107"), "BER", DefaultLimits())
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if n.Class != ClassContext || n.Tag != 2 || len(n.Children) != 1 {
		t.Fatalf("bad context node: %+v", n)
	}
	n2, err := DecodeAndValidate(mustHex(t, "8502cafe"), "BER", DefaultLimits())
	if err != nil {
		t.Fatalf("primitive context: %v", err)
	}
	if n2.Class != ClassContext || n2.Constructed || hex.EncodeToString(n2.Value) != "cafe" {
		t.Fatalf("bad primitive context node: %+v value=%x", n2, n2.Value)
	}
}

func TestDERCanonicalRules(t *testing.T) {
	// indefinite SEQUENCE is BER-ok, DER-bad
	if _, err := DecodeAndValidate(mustHex(t, "30800000"), "BER", DefaultLimits()); err != nil {
		t.Fatalf("empty indefinite SEQUENCE should be BER-ok: %v", err)
	}
	_, err := DecodeAndValidate(mustHex(t, "30800000"), "DER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidEncoding, 1)

	// non-minimal long length
	_, err = DecodeAndValidate(mustHex(t, "02810105"), "DER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidEncoding, 1)

	// non-minimal long tag (1f 80 01 -> tag 1 padded)
	_, err = DecodeAndValidate(mustHex(t, "1f800100"), "DER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidEncoding, 0)

	// canonical version of tag 1 long-form would never appear; short form required
	if _, err := DecodeAndValidate(mustHex(t, "020105"), "DER", DefaultLimits()); err != nil {
		t.Fatalf("02 01 05 must be valid DER: %v", err)
	}

	// DER BOOLEAN must be 00/FF: 01 01 01 is a BER-truthy non-canonical value
	_, err = DecodeAndValidate(mustHex(t, "010101"), "DER", DefaultLimits())
	assertDecodeError(t, err, KindInvalidEncoding, 2)
	if _, err := DecodeAndValidate(mustHex(t, "010101"), "BER", DefaultLimits()); err != nil {
		t.Fatalf("01 01 01 is a legal (non-DER) BER BOOLEAN: %v", err)
	}
}

func TestEncodeFormsAndSelfConsistency(t *testing.T) {
	n := SequenceNode(IntegerNode(1), ContextNode(0, true, []*Node{IntegerNode(2)}, nil))

	def := Encode(n, BER)
	if got := hex.EncodeToString(def); got != "3008020101a003020102" {
		t.Fatalf("definite encoding = %s", got)
	}

	indef := Encode(n, BERIndefinite)
	if got := hex.EncodeToString(indef); got != "3080020101a08002010200000000" {
		t.Fatalf("indefinite encoding = %s", got)
	}
	// indefinite output decodes in BER, not DER
	if _, err := DecodeAndValidate(indef, "BER", DefaultLimits()); err != nil {
		t.Fatalf("indefinite should decode BER: %v", err)
	}
	if _, err := DecodeAndValidate(indef, "DER", DefaultLimits()); err == nil {
		t.Fatal("indefinite must fail DER")
	}

	der := Encode(n, DER)
	if string(der) != string(def) {
		t.Fatalf("DER should equal definite BER for canonical tree")
	}
}

func TestPrimitiveAlwaysDefinite(t *testing.T) {
	// A primitive serialised with the BERIndefinite mode must still use a
	// definite length: indefinite primitives are forbidden by X.690.
	got, err := EncodeChecked(IntegerNode(1), BERIndefinite)
	if err != nil {
		t.Fatalf("primitive child under indefinite mode must encode definite: %v", err)
	}
	if hex.EncodeToString(got) != "020101" {
		t.Fatalf("primitive encoding = %x, want 020101", got)
	}
}

func TestTracerEmitsOffsets(t *testing.T) {
	var steps []Step
	tr := func(s Step) { steps = append(steps, s) }
	in := mustHex(t, "a0800201050000")
	_, err := DecodeTraced(in, DefaultLimits(), tr)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	sawEOC := false
	for _, s := range steps {
		if s.Name == "eoc" && s.Offset == 5 {
			sawEOC = true
		}
	}
	if !sawEOC {
		t.Fatalf("tracer did not report EOC at offset 6: %+v", steps)
	}
}

func TestApplicationAndPrivateRejected(t *testing.T) {
	// 0x40 0x00 = application primitive tag 0 (empty) -> unsupported class
	_, err := DecodeAndValidate(mustHex(t, "4000"), "BER", DefaultLimits())
	assertDecodeError(t, err, KindUnsupported, 0)
}
