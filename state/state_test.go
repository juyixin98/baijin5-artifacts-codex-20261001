package state

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"hpacklab/codec"
	"hpacklab/table"
)

func decodeOne(t *testing.T, d *Decoder, block []byte) []Field {
	t.Helper()
	fields, _, err := d.Decode(block)
	if err != nil {
		t.Fatalf("Decode(%x): %v", block, err)
	}
	return fields
}

func TestDecodeIndexedStatic(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	fields := decodeOne(t, d, []byte{0x82}) // :method: GET
	if len(fields) != 1 || fields[0].Name != ":method" || fields[0].Value != "GET" {
		t.Fatalf("fields=%+v", fields)
	}
}

func TestIndexZeroRejected(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// Indexed representation with index 0: 0x80.
	if _, _, err := d.Decode([]byte{0x80}); !errors.Is(err, table.ErrIndexZero) {
		t.Fatalf("got %v, want ErrIndexZero", err)
	}
	// Literal with incremental indexing, name index 0 is legal (literal
	// name follows), but a literal *without indexing* with name index 0
	// then empty name... use indexed-name form with index 0 in the
	// *value* position is impossible; check name index out of range:
	d2 := NewDecoder(4096, DefaultLimits)
	// 0x7f = literal with indexing, 6-bit prefix saturated -> continuation
	// 0x00 -> index 127, far beyond the table.
	if _, _, err := d2.Decode([]byte{0x7f, 0x40, 0x01, 'a'}); !errors.Is(err, table.ErrIndexOutOfRange) {
		t.Fatalf("got %v, want ErrIndexOutOfRange", err)
	}
}

func TestIndexOutOfRangeRejected(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// Index 62 with empty dynamic table.
	if _, _, err := d.Decode([]byte{0xbe}); !errors.Is(err, table.ErrIndexOutOfRange) {
		t.Fatalf("got %v, want ErrIndexOutOfRange", err)
	}
}

func TestSizeUpdatePlacement(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// A size update after a header field representation is illegal.
	block := []byte{0x82, 0x20} // :method: GET, then size update to 0
	if _, _, err := d.Decode(block); !errors.Is(err, ErrSizeUpdatePlacement) {
		t.Fatalf("got %v, want ErrSizeUpdatePlacement", err)
	}
}

func TestSizeUpdateAtStartLegal(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// Multiple size updates at the start are legal (RFC 7541 4.2 allows
	// up to two; the minimum is what counts). We accept any number at
	// the start, matching x/net behavior of enforcing position only.
	block := []byte{0x3f, 0xe1, 0x1f, 0x20, 0x82} // 4096 -> 0, then GET
	fields := decodeOne(t, d, block)
	if len(fields) != 1 || fields[0].Value != "GET" {
		t.Fatalf("fields=%+v", fields)
	}
	if d.Table().Dyn.MaxSize() != 0 {
		t.Fatalf("max size = %d, want 0", d.Table().Dyn.MaxSize())
	}
}

func TestSizeUpdateTooLarge(t *testing.T) {
	d := NewDecoder(128, DefaultLimits)
	// 0x3f 0x62: 5-bit prefix saturated (31) + 0x62(98) = 129 > 128.
	if _, _, err := d.Decode([]byte{0x3f, 0x62}); !errors.Is(err, ErrSizeUpdateTooLarge) {
		t.Fatalf("got %v, want ErrSizeUpdateTooLarge", err)
	}
}

func TestDesyncAfterFailure(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	if _, _, err := d.Decode([]byte{0x80}); err == nil {
		t.Fatal("first decode must fail")
	}
	// A perfectly valid block must now be rejected: table state can no
	// longer be assumed synchronized.
	if _, _, err := d.Decode([]byte{0x82}); !errors.Is(err, ErrDesync) {
		t.Fatalf("got %v, want ErrDesync", err)
	}
	if !d.Broken() {
		t.Fatal("Broken() must be true after failure")
	}
}

func TestHeaderListLimit(t *testing.T) {
	d := NewDecoder(4096, Limits{MaxHeaderListBytes: 100})
	// Two 36-byte fields fit; the third overflows 100 bytes.
	var block []byte
	for _, name := range []string{"x-a", "x-b", "x-c"} {
		block = append(block, 0x00) // literal without indexing, new name
		block = codec.AppendString(block, name, false)
		block = codec.AppendString(block, "v", false)
	}
	if _, _, err := d.Decode(block); !errors.Is(err, ErrHeaderListTooLarge) {
		t.Fatalf("got %v, want ErrHeaderListTooLarge", err)
	}
}

func TestHeaderCountLimit(t *testing.T) {
	d := NewDecoder(4096, Limits{MaxHeaderCount: 2})
	block := []byte{0x82, 0x83, 0x84} // GET, POST, /
	if _, _, err := d.Decode(block); !errors.Is(err, ErrTooManyHeaders) {
		t.Fatalf("got %v, want ErrTooManyHeaders", err)
	}
}

func TestStringLengthLimitPropagates(t *testing.T) {
	d := NewDecoder(4096, Limits{MaxStringLen: 4})
	var block []byte
	block = append(block, 0x00)
	block = codec.AppendString(block, "x-long-name", false)
	block = codec.AppendString(block, "v", false)
	if _, _, err := d.Decode(block); !errors.Is(err, codec.ErrStringTooLong) {
		t.Fatalf("got %v, want ErrStringTooLong", err)
	}
}

func TestTruncatedBlockRejected(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// Literal without indexing, name "x", value declared 5 bytes, 2 given.
	block := []byte{0x00, 0x01, 'x', 0x05, 'a', 'b'}
	if _, _, err := d.Decode(block); !errors.Is(err, codec.ErrTruncated) {
		t.Fatalf("got %v, want ErrTruncated", err)
	}
}

func TestHuffmanErrorsRejected(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// Name "x", value = huffman string of one byte 0x18 (bad padding).
	block := []byte{0x00, 0x01, 'x', 0x81, 0x18}
	if _, _, err := d.Decode(block); !errors.Is(err, codec.ErrHuffmanPadding) {
		t.Fatalf("padding: got %v, want ErrHuffmanPadding", err)
	}
	d2 := NewDecoder(4096, DefaultLimits)
	// Value = huffman EOS bytes.
	block = []byte{0x00, 0x01, 'x', 0x84, 0xff, 0xff, 0xff, 0xff}
	if _, _, err := d2.Decode(block); !errors.Is(err, codec.ErrHuffmanEOS) {
		t.Fatalf("EOS: got %v, want ErrHuffmanEOS", err)
	}
}

func TestSensitiveFieldNotIndexed(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	// Never-indexed literal: 0x10, name "password", value "secret".
	var block []byte
	block = append(block, 0x10)
	block = codec.AppendString(block, "password", false)
	block = codec.AppendString(block, "secret", false)
	fields := decodeOne(t, d, block)
	if len(fields) != 1 || !fields[0].Sensitive {
		t.Fatalf("fields=%+v, want Sensitive", fields)
	}
	if d.Table().Dyn.Len() != 0 {
		t.Fatalf("never-indexed field entered the dynamic table: %+v", d.Table().Dyn.Entries())
	}
}

func TestDuplicateHeadersPreserved(t *testing.T) {
	e := NewEncoder(4096, false)
	d := NewDecoder(4096, DefaultLimits)
	in := []Field{
		{Name: "x-dup", Value: "1"},
		{Name: "x-dup", Value: "1"},
		{Name: "x-dup", Value: "2"},
	}
	block, _ := e.Encode(in)
	out := decodeOne(t, d, block)
	if len(out) != 3 {
		t.Fatalf("got %d fields, want 3", len(out))
	}
	for i, f := range in {
		if out[i] != f {
			t.Fatalf("field %d: got %+v want %+v", i, out[i], f)
		}
	}
}

func TestRoundTripMultiBlock(t *testing.T) {
	e := NewEncoder(4096, true)
	d := NewDecoder(4096, DefaultLimits)
	blocks := [][]Field{
		{{Name: ":method", Value: "GET"}, {Name: ":path", Value: "/"}, {Name: "x-a", Value: "1"}},
		{{Name: ":method", Value: "GET"}, {Name: ":path", Value: "/"}, {Name: "x-a", Value: "1"}, {Name: "x-b", Value: "2"}},
		{{Name: ":method", Value: "POST"}, {Name: "x-b", Value: "2"}, {Name: "authorization", Value: "sensitive", Sensitive: true}},
	}
	for i, in := range blocks {
		block, _ := e.Encode(in)
		out := decodeOne(t, d, block)
		if len(out) != len(in) {
			t.Fatalf("block %d: got %d fields, want %d", i, len(out), len(in))
		}
		for j, f := range in {
			if out[j].Name != f.Name || out[j].Value != f.Value || out[j].Sensitive != f.Sensitive {
				t.Fatalf("block %d field %d: got %+v want %+v", i, j, out[j], f)
			}
		}
	}
	// Encoder and decoder tables must agree exactly.
	if e.Table().Dyn.Size() != d.Table().Dyn.Size() {
		t.Fatalf("table size mismatch: enc=%d dec=%d", e.Table().Dyn.Size(), d.Table().Dyn.Size())
	}
	ee, de := e.Table().Dyn.Entries(), d.Table().Dyn.Entries()
	if len(ee) != len(de) {
		t.Fatalf("table length mismatch: enc=%d dec=%d", len(ee), len(de))
	}
	for i := range ee {
		if ee[i] != de[i] {
			t.Fatalf("table entry %d: enc=%+v dec=%+v", i, ee[i], de[i])
		}
	}
}

func TestTableShrinkAcrossBlocks(t *testing.T) {
	e := NewEncoder(4096, false)
	d := NewDecoder(4096, DefaultLimits)
	// Fill the table with several entries.
	var many []Field
	for _, v := range []string{"a", "b", "c", "d", "e"} {
		many = append(many, Field{Name: "x-fill-" + v, Value: v})
	}
	block, _ := e.Encode(many)
	decodeOne(t, d, block)
	if d.Table().Dyn.Len() != 5 {
		t.Fatalf("dyn len=%d, want 5", d.Table().Dyn.Len())
	}
	// Shrink to 0: the update must be emitted at the start of the next
	// block, before any field representation.
	e.SetMaxDynamicSize(0)
	block, events := e.Encode([]Field{{Name: ":method", Value: "GET"}})
	if len(events) == 0 || events[0].Kind != EvSizeUpdate {
		t.Fatalf("first event = %+v, want size update", events)
	}
	if block[0]&0xe0 != 0x20 {
		t.Fatalf("first byte %02x is not a size update", block[0])
	}
	decodeOne(t, d, block)
	if d.Table().Dyn.Len() != 0 || d.Table().Dyn.MaxSize() != 0 {
		t.Fatalf("after shrink: len=%d max=%d", d.Table().Dyn.Len(), d.Table().Dyn.MaxSize())
	}
	// Grow again and confirm encoding resumes cleanly.
	e.SetMaxDynamicSize(128)
	block, _ = e.Encode([]Field{{Name: "x-new", Value: "z"}})
	out := decodeOne(t, d, block)
	if len(out) != 1 || out[0].Name != "x-new" {
		t.Fatalf("fields=%+v", out)
	}
	if d.Table().Dyn.Len() != 1 {
		t.Fatalf("dyn len=%d, want 1", d.Table().Dyn.Len())
	}
}

func TestEvictionVisibleAcrossBlocks(t *testing.T) {
	// Small table: each entry costs 34+ bytes, capacity fits ~3.
	e := NewEncoder(128, false)
	d := NewDecoder(128, DefaultLimits)
	var in []Field
	for _, v := range []string{"1", "2", "3", "4", "5"} {
		in = append(in, Field{Name: "x-" + v, Value: v})
	}
	block, _ := e.Encode(in)
	decodeOne(t, d, block)
	if d.Table().Dyn.Evictions == 0 {
		t.Fatal("expected evictions with a 128-byte table")
	}
	if d.Table().Dyn.Size() > 128 {
		t.Fatalf("table size %d exceeds max 128", d.Table().Dyn.Size())
	}
	if e.Table().Dyn.Size() != d.Table().Dyn.Size() {
		t.Fatalf("enc/dec table size mismatch: %d vs %d",
			e.Table().Dyn.Size(), d.Table().Dyn.Size())
	}
}

func TestEventsRecorded(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	_, events, err := d.Decode([]byte{0x82})
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || events[0].Kind != EvIndexed || events[0].Offset != 0 {
		t.Fatalf("events=%+v", events)
	}
	if !strings.Contains(events[0].Detail, ":method") {
		t.Fatalf("event detail not explainable: %q", events[0].Detail)
	}
}

func TestEmptyBlock(t *testing.T) {
	d := NewDecoder(4096, DefaultLimits)
	fields, _, err := d.Decode(nil)
	if err != nil || len(fields) != 0 {
		t.Fatalf("fields=%v err=%v", fields, err)
	}
}

func TestEncoderNeverIndexedBytes(t *testing.T) {
	e := NewEncoder(4096, false)
	block, _ := e.Encode([]Field{{Name: "password", Value: "secret", Sensitive: true}})
	if block[0]&0xf0 != 0x10 {
		t.Fatalf("first byte %02x: sensitive field must use never-indexed form", block[0])
	}
	if bytes.Contains(block, []byte("secret")) == false {
		t.Fatal("value must be present in the block (never-indexed still transmits)")
	}
	if e.Table().Dyn.Len() != 0 {
		t.Fatal("sensitive field entered the encoder dynamic table")
	}
}

// TestRandomGarbageNeverPanics feeds deterministic pseudo-random byte
// strings into fresh decoders: every input must either decode or fail
// with a classified error, and after a failure the decoder must report
// ErrDesync on further input.
func TestRandomGarbageNeverPanics(t *testing.T) {
	rng := uint64(0x2545F4914F6CDD1D)
	next := func() byte {
		// xorshift64
		rng ^= rng << 13
		rng ^= rng >> 7
		rng ^= rng << 17
		return byte(rng)
	}
	for trial := 0; trial < 2000; trial++ {
		n := int(next()) % 40
		block := make([]byte, n)
		for i := range block {
			block[i] = next()
		}
		d := NewDecoder(4096, DefaultLimits)
		_, _, err := d.Decode(block)
		if err == nil {
			continue
		}
		// Any failure must desynchronize the decoder.
		if _, _, err2 := d.Decode([]byte{0x82}); !errors.Is(err2, ErrDesync) {
			t.Fatalf("trial %d: after error %v, got %v, want ErrDesync", trial, err, err2)
		}
	}
}
