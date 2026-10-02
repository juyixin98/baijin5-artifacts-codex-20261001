package hpack

import (
	"errors"
	"testing"
)

func TestDecodeIndexedStatic(t *testing.T) {
	d := NewDecoder()
	fields, err := d.Decode([]byte{0x82}) // :method: GET
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(fields) != 1 || fields[0].Name != ":method" || fields[0].Value != "GET" {
		t.Fatalf("got %v", fields)
	}
}

func TestDecodeLiteralWithoutIndexing(t *testing.T) {
	d := NewDecoder()
	block := EncodeLiteralWithoutIndexing(":path", "/large")
	fields, err := d.Decode(block)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(fields) != 1 || fields[0].Name != ":path" || fields[0].Value != "/large" {
		t.Fatalf("got %v", fields)
	}
}

func TestDecodeLiteralWithIncrementalIndexing(t *testing.T) {
	d := NewDecoder()
	// Literal with incremental indexing, new name "x-a", value "b".
	block := []byte{0x40, 0x03, 'x', '-', 'a', 0x01, 'b'}
	if _, err := d.Decode(block); err != nil {
		t.Fatalf("decode: %v", err)
	}
	// The entry must now be referenceable at dynamic index 62.
	fields, err := d.Decode([]byte{0x80 | 62})
	if err != nil {
		t.Fatalf("decode dynamic: %v", err)
	}
	if len(fields) != 1 || fields[0].Name != "x-a" || fields[0].Value != "b" {
		t.Fatalf("got %v", fields)
	}
}

func TestHuffmanRejected(t *testing.T) {
	d := NewDecoder()
	// Literal without indexing, new name, Huffman bit set on the name.
	block := []byte{0x00, 0x83, 0x00, 0x00, 0x00, 0x00}
	_, err := d.Decode(block)
	if !errors.Is(err, ErrHuffmanUnsupported) {
		t.Fatalf("want ErrHuffmanUnsupported, got %v", err)
	}
}

func TestTruncatedRejected(t *testing.T) {
	d := NewDecoder()
	if _, err := d.Decode([]byte{0x00, 0x05, 'a'}); !errors.Is(err, ErrTruncated) {
		t.Fatalf("want ErrTruncated, got %v", err)
	}
}

func TestInvalidIndexRejected(t *testing.T) {
	d := NewDecoder()
	if _, err := d.Decode([]byte{0x80}); err == nil {
		t.Fatal("index 0 must be rejected")
	}
	if _, err := d.Decode([]byte{0x80 | 62}); err == nil {
		t.Fatal("dynamic index before any insertion must be rejected")
	}
}

func TestIntegerMultiByte(t *testing.T) {
	// 1337 with a 5-bit prefix: 1337-31=1306 -> 0x9a 0x0a (RFC 7541 §5.1 example).
	v, n, err := decodeInt([]byte{0x1f, 0x9a, 0x0a}, 5)
	if err != nil {
		t.Fatalf("decodeInt: %v", err)
	}
	if v != 1337 || n != 3 {
		t.Fatalf("want 1337/3, got %d/%d", v, n)
	}
}

func TestEncodeRoundTrip(t *testing.T) {
	d := NewDecoder()
	var block []byte
	block = append(block, EncodeIndexed(8)...) // :status: 200
	block = append(block, EncodeLiteralWithoutIndexing("content-type", "text/plain")...)
	fields, err := d.Decode(block)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(fields) != 2 ||
		fields[0] != (HeaderField{":status", "200"}) ||
		fields[1] != (HeaderField{"content-type", "text/plain"}) {
		t.Fatalf("got %v", fields)
	}
}

func TestFindStatic(t *testing.T) {
	if idx := FindStatic(":status", "404"); idx != 13 {
		t.Fatalf("want 13, got %d", idx)
	}
	if idx := FindStatic("content-type", "text/plain"); idx != 0 {
		t.Fatalf("value mismatch must not match, got %d", idx)
	}
}
