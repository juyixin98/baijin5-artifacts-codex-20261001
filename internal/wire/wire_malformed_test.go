package wire_test

import (
	"encoding/hex"
	"errors"
	"testing"

	"coapblockwise/internal/wire"
)

func TestDecodeMalformed(t *testing.T) {
	cases := []struct {
		name string
		hex  string
		want error
	}{
		{"too short", "4401", wire.ErrMalformed},
		{"bad version", "00000000", wire.ErrVersion},
		{"tkl runs past datagram", "480112340102", wire.ErrBadTokenLength},
		{"reserved nibble 15 in option delta", "40011234f0", wire.ErrMalformed},
		{"option value past datagram", "4001123412", wire.ErrMalformed},
		{"payload marker with nothing after", "40011234ff", wire.ErrEmptyPayload},
		{"empty code with token", "420012340102", wire.ErrMalformed},
		{"block option reserved szx7", "40011234d10e1f", wire.ErrBlockOption},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			dgram := mustHex(t, tc.hex)
			_, err := wire.Decode(dgram)
			if !errors.Is(err, tc.want) {
				t.Fatalf("Decode(%s) error = %v, want an error wrapping %v",
					tc.hex, err, tc.want)
			}
		})
	}
}

func TestBlockRoundTrip(t *testing.T) {
	blocks := []wire.Block{
		{Num: 0, More: true, SZX: 2},
		{Num: 15, More: false, SZX: 6},
		{Num: 16, More: true, SZX: 0},
		{Num: 4095, More: false, SZX: 3},
		{Num: 4100, More: false, SZX: 5},
		{Num: 1048575, More: false, SZX: 6}, // 20-bit max
	}
	for _, want := range blocks {
		raw, err := wire.EncodeBlock(want)
		if err != nil {
			t.Fatalf("EncodeBlock(%s): %v", want, err)
		}
		got, err := wire.DecodeBlock(raw)
		if err != nil {
			t.Fatalf("DecodeBlock(%x): %v", raw, err)
		}
		if got != want {
			t.Errorf("round trip = %s, want %s (raw=%x)", got, want, raw)
		}
	}
}

func TestBlockRejectsBadValues(t *testing.T) {
	bad := []wire.Block{
		{Num: -1, SZX: 0},
		{Num: 0, SZX: -1},
		{Num: 0, SZX: 7},
		{Num: 1 << 20, SZX: 0},
	}
	for _, b := range bad {
		if _, err := wire.EncodeBlock(b); err == nil {
			t.Errorf("EncodeBlock(%s) succeeded, want error", b)
		}
	}
	if _, err := wire.DecodeBlock([]byte{}); err == nil {
		t.Error("DecodeBlock(empty) succeeded, want error")
	}
	if _, err := wire.DecodeBlock([]byte{0, 0, 0, 0}); err == nil {
		t.Error("DecodeBlock(4 bytes) succeeded, want error")
	}
}

func TestSZXFor(t *testing.T) {
	cases := []struct {
		size int
		szx  int
	}{
		{1, 0}, {16, 0}, {17, 0}, {32, 1}, {31, 0},
		{128, 3}, {1024, 6}, {4096, 6},
	}
	for _, tc := range cases {
		if got := wire.SZXFor(tc.size); got != tc.szx {
			t.Errorf("SZXFor(%d) = %d, want %d", tc.size, got, tc.szx)
		}
	}
}

func TestEncodeCanonicalizesOptionOrder(t *testing.T) {
	// Inserted out of order; Encode must sort so the wire form is canonical.
	m := wire.NewMessage(wire.CON, wire.CodePUT, 1, []byte{1})
	m.Options = m.Options.AddUint(wire.OptBlock2, 0) // 23
	m.Options = m.Options.AddString(wire.OptUriPath, "x") // 11
	raw, err := m.Encode()
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	decoded, err := wire.Decode(raw)
	if err != nil {
		t.Fatalf("Decode own output: %v", err)
	}
	prev := 0
	for i, o := range decoded.Options {
		if o.Number < prev {
			t.Fatalf("option %d at index %d is not ascending", o.Number, i)
		}
		prev = o.Number
	}
	if got := decoded.Options[0].Number; got != wire.OptUriPath {
		t.Errorf("first option = %d, want Uri-Path(%d) after sort",
			got, wire.OptUriPath)
	}
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("bad test hex %q: %v", s, err)
	}
	return b
}
