package codec

import (
	"encoding/hex"
	"errors"
	"testing"
)

func dehex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(stripSpaces(s))
	if err != nil {
		t.Fatalf("bad hex: %v", err)
	}
	return b
}

func stripSpaces(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		if s[i] != ' ' {
			out = append(out, s[i])
		}
	}
	return string(out)
}

func TestIntegerDecodeRFC7541_C1(t *testing.T) {
	// RFC 7541 Appendix C.1 examples (prefix-5 within 0x2a lead octet):
	// 10 = "2a", 1342 = "2a e1 0a".
	cases := []struct {
		lead byte
		rest string
		n    uint
		want uint64
	}{
		{0x2a, "", 5, 10},
		{0x3a, "", 5, 26},
		{0x3f, "9f0a", 5, 1342},
		{0x0e, "", 4, 14},
		{0x0f, "00", 4, 15},
		{0x0f, "1a", 4, 15 + 26},
		{0x7f, "01", 7, 127 + 1},
		{0xff, "00", 8, 255},
	}
	for _, c := range cases {
		got, rem, err := ConsumeInteger(c.lead, dehex(t, c.rest), c.n)
		if err != nil {
			t.Fatalf("ConsumeInteger(%02x,%x,%d) error %v", c.lead, c.rest, c.n, err)
		}
		if got != c.want {
			t.Errorf("ConsumeInteger(%02x,%x,%d) = %d, want %d", c.lead, c.rest, c.n, got, c.want)
		}
		if len(rem) != 0 {
			t.Errorf("unexpected remainder %x", rem)
		}
	}
}

func TestIntegerEncodeDecodeRoundtrip(t *testing.T) {
	for n := uint(1); n <= 8; n++ {
		for _, v := range []uint64{0, 1, (1 << n) - 2, (1 << n) - 1, (1 << n), 1 << 20, MaxIntegerValue} {
			lead := byte(0x3c) // bits outside prefix must be preserved
			enc := AppendInteger(nil, lead, n, v)
			got, rem, err := ConsumeInteger(enc[0], enc[1:], n)
			if err != nil {
				t.Fatalf("n=%d v=%d: %v", n, v, err)
			}
			if got != v || len(rem) != 0 {
				t.Fatalf("n=%d v=%d roundtrip got %d rem %x", n, v, got, rem)
			}
			if enc[0]&^byte((1<<n)-1) != lead&^byte((1<<n)-1) {
				t.Errorf("n=%d v=%d clobbered non-prefix bits: %08b", n, v, enc[0])
			}
		}
	}
}

func TestIntegerErrors(t *testing.T) {
	// Continuation bit set but no following octet.
	if _, _, err := ConsumeInteger(0x1f, nil, 5); !errors.Is(err, ErrTruncatedInteger) {
		t.Fatalf("truncated: want ErrTruncatedInteger, got %v", err)
	}
	// Continuation octets forever: value must trip the 2^62-1 bound.
	run := []byte{0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x01}
	if _, _, err := ConsumeInteger(0x1f, run, 5); !errors.Is(err, ErrIntegerOverflow) {
		t.Fatalf("overflow: want ErrIntegerOverflow, got %v", err)
	}
}

func TestHuffmanRFC7541_C4(t *testing.T) {
	// RFC 7541 Appendix C.4 encoded literals.
	cases := []struct {
		wire string
		want string
	}{
		{"f1e3c2e5f23a6ba0ab90f4ff", "www.example.com"},
		{"a8eb10649cbf", "no-cache"},
		{"6402", "302"},
	}
	for _, c := range cases {
		got, err := DecodeHuffman(dehex(t, c.wire), 0)
		if err != nil {
			t.Fatalf("decode %s: %v", c.wire, err)
		}
		if string(got) != c.want {
			t.Errorf("decode %s = %q, want %q", c.wire, got, c.want)
		}
		// Re-encode and demand byte-identical output.
		enc := AppendHuffman(nil, []byte(c.want))
		if hex.EncodeToString(enc) != stripSpaces(c.wire) {
			t.Errorf("encode %q = %x, want %s", c.want, enc, c.wire)
		}
	}
}

func TestHuffmanMalformed(t *testing.T) {
	cases := []struct {
		name string
		wire string
	}{
		{"8 ones exceeds 7 pad bits", "ff"},
		{"a plus 11 pad bits", "1fff"},
		{"a plus 24 pad bits", "1fffffff"},
		{"29 ones excess pad", "ff9fffffff"},
		{"pad ends inside partial symbol", "52bc30ffffffff"},
		{"full EOS code then symbol", "fffffffc"},
		{"zero bits not valid prefix", "00"},
		{"a then non-EOS padding 110", "1e"},
	}
	for _, c := range cases {
		_, err := DecodeHuffman(dehex(t, c.wire), 0)
		if !errors.Is(err, ErrInvalidHuffman) {
			t.Errorf("%s: want ErrInvalidHuffman, got %v", c.name, err)
		}
	}
}

func TestHuffmanLengthLimit(t *testing.T) {
	// Three encoded '0' symbols (5-bit code 00000) = 00 01; max 2 must
	// fail with the length-limit category, not a huffman error.
	_, err := DecodeHuffman(dehex(t, "0001"), 2)
	if !errors.Is(err, ErrStringLengthLimit) {
		t.Fatalf("want ErrStringLengthLimit, got %v", err)
	}
}

func TestHuffmanEmpty(t *testing.T) {
	got, err := DecodeHuffman(nil, 0)
	if err != nil || len(got) != 0 {
		t.Fatalf("empty input = %q,%v", got, err)
	}
}

func TestStringRoundtrip(t *testing.T) {
	for _, huff := range []bool{false, true} {
		for _, s := range []string{"", "a", "www.example.com", "302", "cookie=abc; def=ghi"} {
			enc := AppendString(nil, []byte(s), huff)
			got, rem, err := ConsumeString(enc[0], enc[1:], 0)
			if err != nil {
				t.Fatalf("huff=%v s=%q: %v", huff, s, err)
			}
			if string(got.Data) != s || len(rem) != 0 || got.Huffman != huff {
				t.Fatalf("huff=%v roundtrip mismatch: %q huff=%v rem=%x", huff, got.Data, got.Huffman, rem)
			}
		}
	}
}

func TestStringTruncated(t *testing.T) {
	// Length says 10, only 3 bytes follow.
	if _, _, err := ConsumeString(0x0a, []byte{1, 2, 3}, 0); !errors.Is(err, ErrTruncatedString) {
		t.Fatalf("want ErrTruncatedString, got %v", err)
	}
	// Length octet continues but the buffer ends.
	if _, _, err := ConsumeString(0xff, nil, 0); !errors.Is(err, ErrTruncatedInteger) {
		t.Fatalf("want ErrTruncatedInteger, got %v", err)
	}
}
