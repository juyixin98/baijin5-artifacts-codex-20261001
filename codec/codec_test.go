package codec

import (
	"bytes"
	"errors"
	"testing"
)

// RFC 7541 Appendix C.1 integer representation examples (public golden
// vectors, transcribed from the specification).
func TestIntegerRFCExamples(t *testing.T) {
	// C.1.1: 10 with a 5-bit prefix encodes as 0x0a.
	if got := AppendInteger(nil, 10, 5, 0); !bytes.Equal(got, []byte{0x0a}) {
		t.Fatalf("C.1.1 encode: got %x", got)
	}
	// C.1.2: 1337 with a 5-bit prefix encodes as 1f 9a 0a.
	if got := AppendInteger(nil, 1337, 5, 0); !bytes.Equal(got, []byte{0x1f, 0x9a, 0x0a}) {
		t.Fatalf("C.1.2 encode: got %x", got)
	}
	v, n, err := DecodeInteger([]byte{0x1f, 0x9a, 0x0a}, 5)
	if err != nil || v != 1337 || n != 3 {
		t.Fatalf("C.1.2 decode: v=%d n=%d err=%v", v, n, err)
	}
	// C.1.3: 42 with an 8-bit prefix encodes as 0x2a.
	if got := AppendInteger(nil, 42, 8, 0); !bytes.Equal(got, []byte{0x2a}) {
		t.Fatalf("C.1.3 encode: got %x", got)
	}
}

func TestIntegerRoundTrip(t *testing.T) {
	for _, prefix := range []uint{1, 4, 5, 6, 7, 8} {
		for _, v := range []uint64{0, 1, 10, 30, 31, 32, 127, 128, 255, 300, 1337, 1 << 20, 1 << 40} {
			enc := AppendInteger(nil, v, prefix, 0)
			got, n, err := DecodeInteger(enc, prefix)
			if err != nil {
				t.Fatalf("prefix=%d v=%d: %v", prefix, v, err)
			}
			if got != v || n != len(enc) {
				t.Fatalf("prefix=%d v=%d: got v=%d n=%d want n=%d", prefix, v, got, n, len(enc))
			}
		}
	}
}

func TestIntegerTruncated(t *testing.T) {
	if _, _, err := DecodeInteger(nil, 5); !errors.Is(err, ErrTruncated) {
		t.Fatalf("empty input: got %v", err)
	}
	// Prefix saturated, continuation bit set, then buffer ends.
	if _, _, err := DecodeInteger([]byte{0x1f, 0x80}, 5); !errors.Is(err, ErrTruncated) {
		t.Fatalf("dangling continuation: got %v", err)
	}
}

func TestIntegerOverflow(t *testing.T) {
	// 10 continuation bytes of 0xff then a terminator: far beyond 64 bits.
	buf := append([]byte{0x1f}, bytes.Repeat([]byte{0xff}, 10)...)
	buf = append(buf, 0x7f)
	if _, _, err := DecodeInteger(buf, 5); !errors.Is(err, ErrIntegerOverflow) {
		t.Fatalf("got %v, want ErrIntegerOverflow", err)
	}
}

func TestIntegerFlagsPreserved(t *testing.T) {
	// The tag bits of the first byte must not leak into the value.
	enc := AppendInteger(nil, 5, 4, 0xf0)
	if enc[0] != 0xf5 {
		t.Fatalf("encode with flags: got %x", enc)
	}
	v, _, err := DecodeInteger([]byte{0xf5}, 4)
	if err != nil || v != 5 {
		t.Fatalf("decode with tag bits: v=%d err=%v", v, err)
	}
}

// RFC 7541 Appendix C.2.1: "custom-key" and "custom-header" as raw
// string literals.
func TestStringRFCExample(t *testing.T) {
	enc := AppendString(nil, "custom-key", false)
	want := []byte{0x0a, 'c', 'u', 's', 't', 'o', 'm', '-', 'k', 'e', 'y'}
	if !bytes.Equal(enc, want) {
		t.Fatalf("encode: got %x want %x", enc, want)
	}
	s, n, err := DecodeString(want, 0)
	if err != nil || s != "custom-key" || n != len(want) {
		t.Fatalf("decode: s=%q n=%d err=%v", s, n, err)
	}
}

func TestStringRoundTrip(t *testing.T) {
	for _, s := range []string{"", "a", "www.example.com", "custom-key", "no-cache", "gzip"} {
		for _, huff := range []bool{false, true} {
			enc := AppendString(nil, s, huff)
			got, n, err := DecodeString(enc, 0)
			if err != nil {
				t.Fatalf("%q huff=%v: %v", s, huff, err)
			}
			if got != s || n != len(enc) {
				t.Fatalf("%q huff=%v: got %q n=%d", s, huff, got, n)
			}
		}
	}
}

func TestStringTruncated(t *testing.T) {
	if _, _, err := DecodeString([]byte{0x05, 'a', 'b'}, 0); !errors.Is(err, ErrTruncated) {
		t.Fatalf("short payload: got %v", err)
	}
	if _, _, err := DecodeString(nil, 0); !errors.Is(err, ErrTruncated) {
		t.Fatalf("empty: got %v", err)
	}
}

func TestStringTooLong(t *testing.T) {
	enc := AppendString(nil, "abcdef", false)
	if _, _, err := DecodeString(enc, 3); !errors.Is(err, ErrStringTooLong) {
		t.Fatalf("declared length over limit: got %v", err)
	}
	// Huffman payload whose decoded form exceeds the limit.
	enc = AppendString(nil, "aaaaaaaaaa", true)
	if _, _, err := DecodeString(enc, 3); !errors.Is(err, ErrStringTooLong) {
		t.Fatalf("decoded length over limit: got %v", err)
	}
}
