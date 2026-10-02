package ber

import (
	"encoding/hex"
	"errors"
	"math/big"
	"testing"
)

func TestConstructorsRoundTrip(t *testing.T) {
	cases := []struct {
		name string
		node *Node
		hex  string
	}{
		{"bool-true", BoolNode(true), "0101ff"},
		{"bool-false", BoolNode(false), "010100"},
		{"null", NullNode(), "0500"},
		{"set", SetNode(IntegerNode(3), IntegerNode(1)), "3106020101020103"}, // DER sorts ascending
		{"sequence-ctor", SequenceNode(IntegerNode(0)), "3003020100"},
		{"context-primitive", ContextNode(5, false, nil, []byte{0xca, 0xfe}), "8502cafe"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := hex.EncodeToString(Encode(tc.node, DER))
			if got != tc.hex {
				t.Fatalf("encoding = %s, want %s", got, tc.hex)
			}
			if _, err := DecodeAndValidate(Encode(tc.node, BER), "BER", DefaultLimits()); err != nil {
				t.Fatalf("self-validation: %v", err)
			}
		})
	}
}

func TestBitStringNodeConstructor(t *testing.T) {
	if _, err := BitStringNode([]byte{0x81}, 3); err == nil {
		t.Fatal("constructor must reject trailing unused bits set")
	}
	n, err := BitStringNode([]byte{0x80}, 3)
	if err != nil {
		t.Fatalf("valid bit string: %v", err)
	}
	if got := hex.EncodeToString(Encode(n, DER)); got != "03020380" {
		t.Fatalf("bit string encoding = %s", got)
	}
}

func TestDERIntegerMinimalityRule(t *testing.T) {
	// X.690 8.3.2: legal minimal multi-octet values whose leading octet is
	// neither 0x00 nor 0xFF (first 9 bits carry significance).
	legal := []string{"02020100", "02027fff", "02028000", "0202feff"}
	for _, h := range legal {
		if _, err := DecodeAndValidate(mustHex(t, h), "DER", DefaultLimits()); err != nil {
			t.Errorf("%s must be minimal DER: %v", h, err)
		}
	}
	// redundant leading octets
	illegal := []string{"02020000", "0202007f", "0202ffff", "0202ff80"}
	for _, h := range illegal {
		if _, err := DecodeAndValidate(mustHex(t, h), "DER", DefaultLimits()); err == nil {
			t.Errorf("%s must be rejected as non-minimal DER", h)
		}
	}
}

func TestBigIntegerNodeSelectedCases(t *testing.T) {
	cases := []struct {
		v   *big.Int
		hex string
	}{
		{big.NewInt(0), "020100"},
		{big.NewInt(127), "02017f"},
		{big.NewInt(128), "02020080"},
		{big.NewInt(-1), "0201ff"},
		{big.NewInt(-128), "020180"},
		{big.NewInt(-129), "0202ff7f"},
	}
	for _, tc := range cases {
		got := hex.EncodeToString(Encode(BigIntegerNode(tc.v), DER))
		if got != tc.hex {
			t.Fatalf("big int %s -> %s, want %s", tc.v, got, tc.hex)
		}
	}
}

func TestSetDEROrderingEnforced(t *testing.T) {
	// SET { INTEGER 3, INTEGER 1 } is structurally fine in BER; DER
	// validation requires ascending encoded order.
	n := SetNode(IntegerNode(3), IntegerNode(1))
	wire := Encode(n, BER) // encode in given order
	if err := n.ValidateBER(DefaultLimits()); err != nil {
		t.Fatalf("BER allows unordered SET: %v", err)
	}
	decoded, err := Decode(wire, DefaultLimits())
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if err := decoded.ValidateDER(DefaultLimits()); err == nil {
		t.Fatal("DER must reject unordered SET")
	} else {
		var de *DecodeError
		if !errors.As(err, &de) || de.Kind != KindInvalidEncoding {
			t.Fatalf("ordering error kind = %v", err)
		}
	}

	// Encoding the same set sorted satisfies DER.
	sorted := SetNode(IntegerNode(1), IntegerNode(3))
	if _, err := DecodeAndValidate(Encode(sorted, DER), "DER", DefaultLimits()); err != nil {
		t.Fatalf("ordered SET must satisfy DER: %v", err)
	}
}

func TestConstructedBitStringBER(t *testing.T) {
	// constructed BIT STRING with two complete segments
	seg1 := &Node{Class: ClassUniversal, Tag: TagBitString, Value: []byte{0x00, 0xAB}}
	seg2 := &Node{Class: ClassUniversal, Tag: TagBitString, Value: []byte{0x03, 0x80}}
	outer := &Node{Class: ClassUniversal, Tag: TagBitString, Constructed: true, Children: []*Node{seg1, seg2}}
	if err := outer.ValidateBER(DefaultLimits()); err != nil {
		t.Fatalf("constructed BIT STRING valid BER: %v", err)
	}
	// DER forbids the constructed form
	if err := outer.ValidateDER(DefaultLimits()); err == nil {
		t.Fatal("DER must reject constructed BIT STRING")
	}
	// non-final segment must carry 0 unused bits
	bad := &Node{Class: ClassUniversal, Tag: TagBitString, Constructed: true, Children: []*Node{
		{Class: ClassUniversal, Tag: TagBitString, Value: []byte{0x01, 0x00}},
		{Class: ClassUniversal, Tag: TagBitString, Value: []byte{0x00, 0xCD}},
	}}
	if err := bad.ValidateBER(DefaultLimits()); err == nil {
		t.Fatal("non-final segment with unused bits must be rejected")
	}
	// empty constructed BIT STRING is invalid
	empty := &Node{Class: ClassUniversal, Tag: TagBitString, Constructed: true}
	if err := empty.ValidateBER(DefaultLimits()); err == nil {
		t.Fatal("empty constructed BIT STRING must be rejected")
	}
}

func TestConstructedOctetStringBEROnly(t *testing.T) {
	// BER allows constructed OCTET STRING; DER requires primitive form.
	n := &Node{
		Class:       ClassUniversal,
		Tag:         TagOctetString,
		Constructed: true,
		Children: []*Node{
			{Class: ClassUniversal, Tag: TagOctetString, Value: []byte("hi")},
		},
	}
	if err := n.ValidateBER(DefaultLimits()); err != nil {
		t.Fatalf("constructed OCTET STRING BER: %v", err)
	}
	if err := n.ValidateDER(DefaultLimits()); err == nil {
		t.Fatal("DER must reject constructed OCTET STRING")
	}
}

func TestNullAndBooleanRules(t *testing.T) {
	if _, err := DecodeAndValidate(mustHex(t, "050100"), "BER", DefaultLimits()); err == nil {
		t.Fatal("NULL with content must fail")
	}
	if _, err := DecodeAndValidate(mustHex(t, "01020000"), "BER", DefaultLimits()); err == nil {
		t.Fatal("BOOLEAN with two octets must fail")
	}
}

func TestEncodeErrors(t *testing.T) {
	if _, err := EncodeChecked(nil, BER); err == nil {
		t.Fatal("nil node must error")
	}
	// application class outside profile
	if _, err := EncodeChecked(&Node{Class: ClassApplication, Tag: 1, Value: []byte{0}}, BER); err == nil {
		t.Fatal("application class must error")
	}
	// primitive without content
	if _, err := EncodeChecked(&Node{Class: ClassUniversal, Tag: TagInteger}, BER); err == nil {
		t.Fatal("missing primitive content must error")
	}
	// oversized tag
	if _, err := EncodeChecked(&Node{Class: ClassContext, Tag: 1 << 30, Value: []byte{}}, BER); err == nil {
		t.Fatal("oversized tag must error")
	}
}

func TestBitStringValueAccessor(t *testing.T) {
	n, err := DecodeAndValidate(mustHex(t, "03020380"), "BER", DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	data, unused, err := BitStringValue(n)
	if err != nil || unused != 3 || hex.EncodeToString(data) != "80" {
		t.Fatalf("accessor: data=%x unused=%d err=%v", data, unused, err)
	}
	if _, _, err := BitStringValue(IntegerNode(1)); err == nil {
		t.Fatal("accessor must reject non-BIT STRING")
	}
	if _, err := RawInteger(IntegerNode(1)); err != nil {
		t.Fatalf("RawInteger: %v", err)
	}
}

func TestClassStringAndUnsupportedUniversal(t *testing.T) {
	if ClassUniversal.String() != "universal" || ClassContext.String() != "context" {
		t.Fatal("class strings wrong")
	}
	// unsupported universal tag (OID = 0x06) is in the profile's reject set
	if _, err := DecodeAndValidate(mustHex(t, "06012a"), "BER", DefaultLimits()); err == nil {
		t.Fatal("OID must be outside the restricted profile")
	}
}
