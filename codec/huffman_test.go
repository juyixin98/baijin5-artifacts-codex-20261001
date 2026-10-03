package codec

import (
	"errors"
	"testing"
)

// RFC 7541 Appendix C.4.1: "www.example.com" Huffman-encodes to
// f1e3 c2e5 f23a 6ba0 ab90 f4ff (public golden vector).
func TestHuffmanRFCVector(t *testing.T) {
	want := []byte{0xf1, 0xe3, 0xc2, 0xe5, 0xf2, 0x3a, 0x6b, 0xa0, 0xab, 0x90, 0xf4, 0xff}
	got := AppendHuffman(nil, "www.example.com")
	if string(got) != string(want) {
		t.Fatalf("encode: got %x want %x", got, want)
	}
	s, err := DecodeHuffman(want)
	if err != nil || s != "www.example.com" {
		t.Fatalf("decode: s=%q err=%v", s, err)
	}
}

// RFC 7541 Appendix C.4.2: "no-cache" -> a8eb 1064 9cbf.
func TestHuffmanRFCVector2(t *testing.T) {
	want := []byte{0xa8, 0xeb, 0x10, 0x64, 0x9c, 0xbf}
	got := AppendHuffman(nil, "no-cache")
	if string(got) != string(want) {
		t.Fatalf("encode: got %x want %x", got, want)
	}
	s, err := DecodeHuffman(want)
	if err != nil || s != "no-cache" {
		t.Fatalf("decode: s=%q err=%v", s, err)
	}
}

func TestHuffmanRoundTrip(t *testing.T) {
	corpus := []string{
		"", "a", "0", "/", "GET", "www.example.com", "custom-key",
		"Mon, 21 Oct 2013 20:13:21 GMT", "https://www.example.com",
		"foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1",
		string([]byte{0x00, 0x01, 0xfe, 0xff}), // non-ASCII bytes
	}
	for _, s := range corpus {
		enc := AppendHuffman(nil, s)
		if got := HuffmanEncodedLen(s); got != len(enc) {
			t.Fatalf("%q: HuffmanEncodedLen=%d, encoded %d bytes", s, got, len(enc))
		}
		got, err := DecodeHuffman(enc)
		if err != nil {
			t.Fatalf("%q: %v", s, err)
		}
		if got != s {
			t.Fatalf("round trip: got %q want %q", got, s)
		}
	}
}

func TestHuffmanEOSRejected(t *testing.T) {
	// EOS is 30 one-bits; 32 one-bits decode as EOS followed by 2 padding
	// bits, so the EOS symbol itself appears in the payload.
	eos := []byte{0xff, 0xff, 0xff, 0xff}
	if _, err := DecodeHuffman(eos); !errors.Is(err, ErrHuffmanEOS) {
		t.Fatalf("got %v, want ErrHuffmanEOS", err)
	}
}

func TestHuffmanBadPaddingRejected(t *testing.T) {
	// "a" encodes as 00011 (5 bits). Valid padding pads with 1s:
	// 00011 111 = 0x1f. Padding with 0s (00011 000 = 0x18) is invalid.
	if _, err := DecodeHuffman([]byte{0x18}); !errors.Is(err, ErrHuffmanPadding) {
		t.Fatalf("zero padding: got %v, want ErrHuffmanPadding", err)
	}
	// More than 7 leftover bits: a single 5-bit symbol leaves 3 bits,
	// but 8 bits of 1s after a symbol is 8 leftover bits -> invalid.
	// "a" = 00011, then 8 one-bits: 00011 111 11111 1(11) -> 0x1f 0xff 0xff
	// leaves 8+3-5... craft directly: byte 0xff followed by 0xff decodes
	// 16 one-bits; EOS is 30, so after 16 bits pending=16 > 7.
	if _, err := DecodeHuffman([]byte{0xff, 0xff}); !errors.Is(err, ErrHuffmanPadding) {
		t.Fatalf("long padding: got %v, want ErrHuffmanPadding", err)
	}
}

func TestHuffmanEmpty(t *testing.T) {
	s, err := DecodeHuffman(nil)
	if err != nil || s != "" {
		t.Fatalf("empty: s=%q err=%v", s, err)
	}
	if got := AppendHuffman(nil, ""); len(got) != 0 {
		t.Fatalf("encode empty: got %x", got)
	}
}
