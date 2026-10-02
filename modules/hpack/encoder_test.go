package hpack

import "testing"

// TestEncoderExactRFC7541_C3_Block1 asserts byte-for-byte output matching
// RFC 7541 Appendix C.3 request 1 (literal without indexing... note the RFC
// example uses incremental indexing for :authority, 0x41 name index 1).
func TestEncoderExactRFC7541_C3_Block1(t *testing.T) {
	e := NewEncoder(EncoderOptions{MaxTableSize: 4096, Huffman: false})
	fields := []HeaderField{
		{Name: ":method", Value: "GET"},
		{Name: ":scheme", Value: "http"},
		{Name: ":path", Value: "/"},
		{Name: ":authority", Value: "www.example.com"},
	}
	got := e.EncodeBlock(fields)
	want := hx(t, "8286 8441 0f77 7777 2e65 7861 6d70 6c65 2e63 6f6d")
	if string(got) != string(want) {
		t.Fatalf("encoded = %x, want %x", got, want)
	}
}

func TestEncoderHuffmanExact(t *testing.T) {
	e := NewEncoder(EncoderOptions{MaxTableSize: 4096, Huffman: true})
	fields := []HeaderField{
		{Name: ":method", Value: "GET"},
		{Name: ":scheme", Value: "http"},
		{Name: ":path", Value: "/"},
		{Name: ":authority", Value: "www.example.com"},
	}
	got := e.EncodeBlock(fields)
	// RFC 7541 C.4 block 1: indexed 82 86 84, literal incremental name idx 1
	// (0x41) + huffman length 11 + encoded host + ff.
	want := hx(t, "8286 8441 8cf1 e3c2 e5f2 3a6b a0ab 90f4 ff")
	if string(got) != string(want) {
		t.Fatalf("encoded = %x, want %x", got, want)
	}
}

func TestEncoderDecoderRoundTrip(t *testing.T) {
	e := NewEncoder(EncoderOptions{MaxTableSize: 4096, Huffman: true})
	d := newTestDecoder(t, 4096)
	blocks := [][]HeaderField{
		{
			{Name: ":method", Value: "GET"},
			{Name: ":path", Value: "/"},
			{Name: ":scheme", Value: "https"},
			{Name: ":authority", Value: "example.com"},
			{Name: "x-trace", Value: "abc123"},
		},
		{
			{Name: ":method", Value: "GET"},
			{Name: ":path", Value: "/assets/app.js"},
			{Name: ":scheme", Value: "https"},
			{Name: ":authority", Value: "example.com"},
			{Name: "x-trace", Value: "abc123"},
			{Name: "accept-encoding", Value: "gzip"},
		},
		{
			{Name: ":method", Value: "POST"},
			{Name: ":path", Value: "/api"},
			{Name: "cookie", Value: "session=deadbeef", Sensitive: true},
		},
	}
	for i, b := range blocks {
		wire := e.EncodeBlock(b)
		got, err := d.DecodeBlock(wire)
		if err != nil {
			t.Fatalf("block %d decode: %v", i, err)
		}
		if len(got) != len(b) {
			t.Fatalf("block %d: %d fields, want %d", i, len(got), len(b))
		}
		for j := range b {
			if got[j].Name != b[j].Name || got[j].Value != b[j].Value {
				t.Fatalf("block %d field %d = %q:%q, want %q:%q", i, j, got[j].Name, got[j].Value, b[j].Name, b[j].Value)
			}
			if got[j].Sensitive != b[j].Sensitive {
				t.Fatalf("block %d field %d sensitive=%t want %t", i, j, got[j].Sensitive, b[j].Sensitive)
			}
		}
	}
	// The sensitive cookie must never have entered either table.
	if got, _ := d.dt.at(1); got.Name == "cookie" {
		t.Fatal("sensitive cookie leaked into decoder dynamic table")
	}
}

func TestEncoderSizeUpdateLeadsBlock(t *testing.T) {
	e := NewEncoder(EncoderOptions{MaxTableSize: 4096, Huffman: false})
	// Fill with one entry first.
	e.EncodeBlock([]HeaderField{{Name: "x-big", Value: stringsRepeat("a", 100)}})
	if e.DynamicTableLen() != 1 {
		t.Fatal("seed entry missing")
	}
	// Peer shrinks to 32 bytes; entry cost 136 cannot survive.
	e.UpdateMaxTableSize(32)
	wire := e.EncodeBlock([]HeaderField{{Name: ":method", Value: "GET"}})
	// First representation must be the size update 001xxxxx.
	if wire[0]&0xe0 != 0x20 {
		t.Fatalf("first byte %02x is not a size update prefix", wire[0])
	}
	// 32 in 5-bit prefix fits directly (0x20 | 32 would overflow: 32 > 31,
	// so it encodes 3f 01).
	if wire[0] != 0x3f || wire[1] != 0x01 {
		t.Fatalf("size update bytes = %02x%02x, want 3f01", wire[0], wire[1])
	}
	if e.DynamicTableSize() != 0 || e.DynamicTableLen() != 0 {
		t.Fatalf("table not emptied by shrink: size=%d len=%d", e.DynamicTableSize(), e.DynamicTableLen())
	}
	// A second block must not repeat the update.
	wire2 := e.EncodeBlock([]HeaderField{{Name: ":method", Value: "GET"}})
	if wire2[0]&0xe0 == 0x20 {
		t.Fatal("size update repeated on a later block")
	}
}

func TestEncoderNeverIndexesSensitive(t *testing.T) {
	e := NewEncoder(EncoderOptions{MaxTableSize: 4096})
	// Prime the table with a non-sensitive copy of the same name so a naive
	// encoder might reference it; the sensitive field still must not index.
	e.EncodeBlock([]HeaderField{{Name: "token", Value: "benign"}})
	wire := e.EncodeBlock([]HeaderField{{Name: "token", Value: "real-secret", Sensitive: true}})
	if wire[0]&0xf0 != 0x10 {
		t.Fatalf("sensitive wire %x does not start with never-indexed 0001", wire)
	}
	// The name may be referenced by index (safe, name alone is not secret),
	// but no incremental insertion may happen: table length stays 1.
	if e.DynamicTableLen() != 1 {
		t.Fatalf("table len = %d, want 1", e.DynamicTableLen())
	}
}

func stringsRepeat(s string, n int) string {
	out := make([]byte, 0, len(s)*n)
	for i := 0; i < n; i++ {
		out = append(out, s...)
	}
	return string(out)
}
