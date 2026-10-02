package oracle

import (
	"encoding/hex"
	"testing"
)

func TestIntegerEncodersProduceDecodableTLVs(t *testing.T) {
	cases := []struct {
		name string
		wire []byte
	}{
		{"int64", IntegerBytes(-256)},
		{"raw-256", IntegerBytesRaw([]byte{0x01, 0x00})},
		{"raw-neg256", IntegerBytesRaw([]byte{0xFF, 0x00})},
		{"huge", IntegerBytesRaw(append([]byte{0x00}, make([]byte, 300)...))},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res := Decode(tc.wire)
			if !res.OK {
				t.Fatalf("oracle rejects its own encoding: %v", res.ParseError)
			}
			if res.Rest != 0 || res.Node.Tag != 2 {
				t.Fatalf("unexpected decode: rest=%d tag=%d", res.Rest, res.Node.Tag)
			}
		})
	}
}

func TestSequenceAndContextEncoders(t *testing.T) {
	seq := SequenceBytes(IntegerBytes(1), IntegerBytes(2))
	if got := hex.EncodeToString(seq); got != "3006020101020102" {
		t.Fatalf("sequence = %s", got)
	}
	ctx := ContextBytes(0, IntegerBytes(7))
	if got := hex.EncodeToString(ctx); got != "a003020107" {
		t.Fatalf("context = %s", got)
	}
	// both decode with zero remainder
	for _, w := range [][]byte{seq, ctx} {
		if r := Decode(w); !r.OK || r.Rest != 0 {
			t.Fatalf("encoded wire rejected: %+v", r.ParseError)
		}
	}
}

func TestIndefiniteWrap(t *testing.T) {
	got := hex.EncodeToString(IndefiniteWrap(0x30, IntegerBytes(5)))
	if got != "30800201050000" {
		t.Fatalf("indefinite wrap = %s", got)
	}
	res := Decode([]byte{0x30, 0x80, 0x02, 0x01, 0x05, 0x00, 0x00})
	if !res.OK {
		t.Fatalf("indefinite wire rejected: %v", res.ParseError)
	}
}

func TestConvertClassMapping(t *testing.T) {
	res := Decode([]byte{0x85, 0x01, 0x42})
	if !res.OK {
		t.Fatalf("context primitive: %v", res.ParseError)
	}
	if res.Node.Class != ClassContext || res.Node.Tag != 5 {
		t.Fatalf("class=%d tag=%d", res.Node.Class, res.Node.Tag)
	}
}
