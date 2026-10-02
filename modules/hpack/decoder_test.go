package hpack

import (
	"encoding/hex"
	"errors"
	"strings"
	"testing"
)

func hx(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(strings.NewReplacer(" ", "", "\n", "", "\t", "").Replace(s))
	if err != nil {
		t.Fatalf("bad hex %q: %v", s, err)
	}
	return b
}

func newTestDecoder(t *testing.T, max uint32) *Decoder {
	t.Helper()
	return NewDecoder(DecoderOptions{MaxTableSize: max, Limits: DefaultLimits()})
}

func wantErrKind(t *testing.T, err error, k Kind) {
	t.Helper()
	var he *Error
	if !errors.As(err, &he) {
		t.Fatalf("want *hpack.Error %s, got %T: %v", k, err, err)
	}
	if he.Kind != k {
		t.Fatalf("error kind = %s, want %s (detail: %s)", he.Kind, k, he.Detail)
	}
}

// dynamicSnapshot returns the table newest-first, as protocol indices see it.
func dynamicSnapshot(d *Decoder) []HeaderField {
	n := d.dt.len()
	out := make([]HeaderField, 0, n)
	for i := 1; i <= n; i++ {
		f, _ := d.dt.at(i)
		out = append(out, f)
	}
	return out
}

// TestRFC7541_C3 transcribes the section C.3 request series (no Huffman)
// from RFC 7541, asserting exact emitted fields, dynamic table byte sizes
// (57/110/164) and newest-first table content after each block.
func TestRFC7541_C3(t *testing.T) {
	d := newTestDecoder(t, 4096)

	hf, err := d.DecodeBlock(hx(t, "8286 8441 0f77 7777 2e65 7861 6d70 6c65 2e63 6f6d"))
	if err != nil {
		t.Fatal(err)
	}
	assertFields(t, hf, [][2]string{
		{":method", "GET"}, {":scheme", "http"}, {":path", "/"}, {":authority", "www.example.com"},
	})
	if d.DynamicTableSize() != 57 {
		t.Fatalf("size = %d, want 57", d.DynamicTableSize())
	}
	assertSnapshot(t, d, [][2]string{{":authority", "www.example.com"}})

	hf, err = d.DecodeBlock(hx(t, "8286 84be 5808 6e6f 2d63 6163 6865"))
	if err != nil {
		t.Fatal(err)
	}
	assertFields(t, hf, [][2]string{
		{":method", "GET"}, {":scheme", "http"}, {":path", "/"},
		{":authority", "www.example.com"}, {"cache-control", "no-cache"},
	})
	if d.DynamicTableSize() != 110 {
		t.Fatalf("size = %d, want 110", d.DynamicTableSize())
	}
	assertSnapshot(t, d, [][2]string{
		{"cache-control", "no-cache"}, {":authority", "www.example.com"},
	})

	hf, err = d.DecodeBlock(hx(t, "8287 85bf 400a 6375 7374 6f6d 2d6b 6579 0c63 7573 746f 6d2d 7661 6c75 65"))
	if err != nil {
		t.Fatal(err)
	}
	assertFields(t, hf, [][2]string{
		{":method", "GET"}, {":scheme", "https"}, {":path", "/index.html"},
		{":authority", "www.example.com"}, {"custom-key", "custom-value"},
	})
	if d.DynamicTableSize() != 164 {
		t.Fatalf("size = %d, want 164", d.DynamicTableSize())
	}
	assertSnapshot(t, d, [][2]string{
		{"custom-key", "custom-value"},
		{"cache-control", "no-cache"},
		{":authority", "www.example.com"},
	})
}

// TestRFC7541_C4 is the same request series with Huffman-encoded literals.
func TestRFC7541_C4(t *testing.T) {
	d := newTestDecoder(t, 4096)
	hf, err := d.DecodeBlock(hx(t, "8286 8441 8cf1 e3c2 e5f2 3a6b a0ab 90f4 ff"))
	if err != nil {
		t.Fatal(err)
	}
	assertFields(t, hf, [][2]string{
		{":method", "GET"}, {":scheme", "http"}, {":path", "/"}, {":authority", "www.example.com"},
	})
	if d.DynamicTableSize() != 57 {
		t.Fatalf("size = %d, want 57", d.DynamicTableSize())
	}

	hf, err = d.DecodeBlock(hx(t, "8286 84be 5886 a8eb 1064 9cbf"))
	if err != nil {
		t.Fatal(err)
	}
	if hf[4].Name != "cache-control" || hf[4].Value != "no-cache" {
		t.Fatalf("field 4 = %q %q", hf[4].Name, hf[4].Value)
	}
	if d.DynamicTableSize() != 110 {
		t.Fatalf("size = %d, want 110", d.DynamicTableSize())
	}

	hf, err = d.DecodeBlock(hx(t, "8287 85bf 4088 25a8 49e9 5ba9 7d7f 8925 a849 e95b b8e8 b4bf"))
	if err != nil {
		t.Fatal(err)
	}
	assertFields(t, hf[4:], [][2]string{{"custom-key", "custom-value"}})
	if d.DynamicTableSize() != 164 {
		t.Fatalf("size = %d, want 164", d.DynamicTableSize())
	}
}

// TestRFC7541_C5 covers eviction with a 256-octet table maximum, including
// the exact surviving set and byte sizes from Appendix C.5/C.6.
func TestRFC7541_C5(t *testing.T) {
	d := newTestDecoder(t, 256)

	_, err := d.DecodeBlock(hx(t, `
4803 3330 3258 0770 7269 7661 7465 611d
4d6f 6e2c 2032 3120 4f63 7420 3230 3133
2032 303a 3133 3a32 3120 474d 546e 1768
7474 7073 3a2f 2f77 7777 2e65 7861 6d70
6c65 2e63 6f6d`))
	if err != nil {
		t.Fatal(err)
	}
	if d.DynamicTableSize() != 222 {
		t.Fatalf("after block 1 size = %d, want 222", d.DynamicTableSize())
	}
	assertSnapshot(t, d, [][2]string{
		{"location", "https://www.example.com"},
		{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
		{"cache-control", "private"},
		{":status", "302"},
	})

	_, err = d.DecodeBlock(hx(t, "4803 3330 37c1 c0bf"))
	if err != nil {
		t.Fatal(err)
	}
	if d.DynamicTableSize() != 222 {
		t.Fatalf("after block 2 size = %d, want 222", d.DynamicTableSize())
	}
	assertSnapshot(t, d, [][2]string{
		{":status", "307"},
		{"location", "https://www.example.com"},
		{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
		{"cache-control", "private"},
	})

	hf, err := d.DecodeBlock(hx(t, `
88c1 611d 4d6f 6e2c 2032 3120 4f63 7420
3230 3133 2032 303a 3133 3a32 3220 474d
54c0 5a04 677a 6970 7738 666f 6f3d 4153
444a 4b48 514b 425a 584f 5157 454f 5049
5541 5851 5745 4f49 553b 206d 6178 2d61
6765 3d33 3630 303b 2076 6572 7369 6f6e
3d31`))
	if err != nil {
		t.Fatal(err)
	}
	if len(hf) != 6 || hf[0].Value != "200" {
		t.Fatalf("block 3 fields = %v", hf)
	}
	if d.DynamicTableSize() != 215 {
		t.Fatalf("after block 3 size = %d, want 215", d.DynamicTableSize())
	}
	assertSnapshot(t, d, [][2]string{
		{"set-cookie", "foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1"},
		{"content-encoding", "gzip"},
		{"date", "Mon, 21 Oct 2013 20:13:22 GMT"},
	})
}

func assertFields(t *testing.T, got []HeaderField, want [][2]string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %d fields %v, want %d %v", len(got), got, len(want), want)
	}
	for i := range want {
		if got[i].Name != want[i][0] || got[i].Value != want[i][1] {
			t.Fatalf("field %d = (%q,%q), want (%q,%q)", i, got[i].Name, got[i].Value, want[i][0], want[i][1])
		}
	}
}

func assertSnapshot(t *testing.T, d *Decoder, want [][2]string) {
	t.Helper()
	got := dynamicSnapshot(d)
	if len(got) != len(want) {
		t.Fatalf("table has %d entries %v, want %d %v", len(got), got, len(want), want)
	}
	for i := range want {
		if got[i].Name != want[i][0] || got[i].Value != want[i][1] {
			t.Fatalf("table[%d] = (%q,%q), want (%q,%q); full=%v", i, got[i].Name, got[i].Value, want[i][0], want[i][1], got)
		}
	}
}

func TestIndexZero(t *testing.T) {
	// 0x80 is the indexed representation with index 0.
	d := newTestDecoder(t, 4096)
	_, err := d.DecodeBlock([]byte{0x80})
	wantErrKind(t, err, KindIndexZero)
}

func TestIndexOutOfRange(t *testing.T) {
	d := newTestDecoder(t, 4096)
	// Static table ends at 61; index 62 with an empty dynamic table.
	_, err := d.DecodeBlock([]byte{0xbe})
	wantErrKind(t, err, KindIndexOutOfRange)
}

func TestHuffmanErrorCategory(t *testing.T) {
	d := newTestDecoder(t, 4096)
	// Literal incremental, name index 0, name len 1 with H bit, then an
	// all-ones byte (8 EOS padding bits) which is invalid.
	block := []byte{0x40, 0x81, 0xff}
	_, err := d.DecodeBlock(block)
	wantErrKind(t, err, KindHuffmanInvalid)
}

func TestTruncationCategories(t *testing.T) {
	// Each malformed block gets its own decoder: a failure poisons it.
	d1 := newTestDecoder(t, 4096)
	// Indexed integer whose continuation never ends within the block.
	_, err := d1.DecodeBlock([]byte{0xff})
	wantErrKind(t, err, KindIntegerTruncated)

	d2 := newTestDecoder(t, 4096)
	// Literal with name length 10 but no bytes following.
	_, err = d2.DecodeBlock([]byte{0x40, 0x0a})
	wantErrKind(t, err, KindStringTruncated)

	d3 := newTestDecoder(t, 4096)
	// Name present, value missing entirely.
	_, err = d3.DecodeBlock([]byte{0x40, 0x01, 'a'})
	wantErrKind(t, err, KindTruncatedBlock)
}

func TestSizeUpdatePosition(t *testing.T) {
	d := newTestDecoder(t, 256)
	// A size update at the very start is legal. 31 in a 5-bit prefix needs
	// one continuation octet: 3f 00.
	if _, err := d.DecodeBlock(hx(t, "3f00")); err != nil {
		t.Fatalf("legal leading size update: %v", err)
	}
	// After one real field, a size update must be rejected.
	_, err := d.DecodeBlock([]byte{0x82, 0x20})
	wantErrKind(t, err, KindSizeUpdatePosition)
}

func TestSizeUpdateTooLarge(t *testing.T) {
	d := newTestDecoder(t, 256)
	// Request 4096 (3f e1 9f 00) while only 256 was advertised.
	_, err := d.DecodeBlock(hx(t, "3fe19f00"))
	wantErrKind(t, err, KindSizeUpdateTooLarge)
}

func TestSizeUpdateShrinkEvictsByByteCost(t *testing.T) {
	d := newTestDecoder(t, 4096)
	// Insert two entries: "a:1" and "b:2" each cost 1+1+32 = 34 octets.
	if _, err := d.DecodeBlock(hx(t, "4001 61 01 31 4001 62 01 32")); err != nil {
		t.Fatal(err)
	}
	if d.DynamicTableSize() != 68 || d.DynamicTableLen() != 2 {
		t.Fatalf("pre-shrink size=%d len=%d", d.DynamicTableSize(), d.DynamicTableLen())
	}
	// Shrink to 40: 34+34 > 40, so the oldest ("a","1") is evicted by byte
	// cost and the newest 34-octet entry survives. 40 in a 5-bit prefix is
	// encoded 3f 09 (31 + continuation 9).
	if _, err := d.DecodeBlock(hx(t, "3f09")); err != nil {
		t.Fatal(err)
	}
	if d.DynamicTableLen() != 1 {
		t.Fatalf("len after shrink = %d, want 1", d.DynamicTableLen())
	}
	assertSnapshot(t, d, [][2]string{{"b", "2"}})
	if d.DynamicTableSize() != 34 {
		t.Fatalf("size after shrink = %d, want 34", d.DynamicTableSize())
	}
}

func TestOversizedEntrySelfEvicts(t *testing.T) {
	d := newTestDecoder(t, 64)
	// Field with cost 10 + 10 + 32 = 52 fits once; a second entry of the
	// same cost forces eviction of the first (52+52 > 64).
	mk := func(name, val string) []byte {
		b := []byte{0x40, byte(len(name))}
		b = append(b, name...)
		b = append(b, byte(len(val)))
		b = append(b, val...)
		return b
	}
	block := append(mk("aaaaaaaaaa", "bbbbbbbbbb"), mk("cccccccccc", "dddddddddd")...)
	hf, err := d.DecodeBlock(block)
	if err != nil {
		t.Fatal(err)
	}
	if len(hf) != 2 {
		t.Fatalf("emitted %d fields, want 2", len(hf))
	}
	if d.DynamicTableLen() != 1 {
		t.Fatalf("table len = %d, want 1 (oldest evicted by byte cost)", d.DynamicTableLen())
	}
	assertSnapshot(t, d, [][2]string{{"cccccccccc", "dddddddddd"}})

	// An entry bigger than the maximum self-evicts: emitted but not stored.
	big := mk("xxxxxxxxxx", strings.Repeat("y", 40)) // cost 82 > 64
	hf, err = d.DecodeBlock(big)
	if err != nil {
		t.Fatal(err)
	}
	if len(hf) != 1 || d.DynamicTableLen() != 0 || d.DynamicTableSize() != 0 {
		t.Fatalf("oversized: len=%d size=%d fields=%d", d.DynamicTableLen(), d.DynamicTableSize(), len(hf))
	}
}

func TestSensitiveNeverIndexed(t *testing.T) {
	e := NewEncoder(EncoderOptions{MaxTableSize: 4096})
	wire := e.EncodeBlock([]HeaderField{{Name: "authorization", Value: "Bearer s3cret", Sensitive: true}})
	// Never-indexed prefix: top nibble 0001 (0x10). Name index 0 because the
	// field has not been indexed, followed by the raw name.
	if wire[0]&0xf0 != 0x10 {
		t.Fatalf("wire = %x, want never-indexed prefix 0x1x", wire)
	}
	if e.DynamicTableLen() != 0 {
		t.Fatal("encoder indexed a sensitive field")
	}
	d := newTestDecoder(t, 4096)
	hf, err := d.DecodeBlock(wire)
	if err != nil {
		t.Fatal(err)
	}
	if !hf[0].Sensitive || d.DynamicTableLen() != 0 {
		t.Fatalf("sensitive=%t tableLen=%d, want true/0", hf[0].Sensitive, d.DynamicTableLen())
	}
}

func TestDuplicateHeaderPreserved(t *testing.T) {
	d := newTestDecoder(t, 4096)
	// Two identical incremental literals: both must be emitted and both
	// inserted (duplicates are legal in HPACK).
	one := hx(t, "4001 78 01 31")
	block := append(append([]byte{}, one...), one...)
	hf, err := d.DecodeBlock(block)
	if err != nil {
		t.Fatal(err)
	}
	if len(hf) != 2 || hf[0].Value != "1" || hf[1].Value != "1" {
		t.Fatalf("duplicates not preserved: %v", hf)
	}
	if d.DynamicTableLen() != 2 {
		t.Fatalf("table len = %d, want 2 duplicate entries", d.DynamicTableLen())
	}
}

func TestPoisoningStopsDecoder(t *testing.T) {
	d := newTestDecoder(t, 4096)
	if _, err := d.DecodeBlock([]byte{0x80}); err == nil {
		t.Fatal("expected index-zero failure")
	}
	if !d.Poisoned() {
		t.Fatal("decoder not poisoned after failure")
	}
	// Every later block, even a perfectly valid one, must be refused.
	_, err := d.DecodeBlock([]byte{0x82})
	wantErrKind(t, err, KindPoisoned)
}

func TestConnectionStateIsolation(t *testing.T) {
	d1 := newTestDecoder(t, 4096)
	d2 := newTestDecoder(t, 4096)
	// d1 learns a custom dynamic entry; d2 must not resolve it.
	block := hx(t, "400a 6375 7374 6f6d 2d6b 6579 0c63 7573 746f 6d2d 7661 6c75 65")
	if _, err := d1.DecodeBlock(block); err != nil {
		t.Fatal(err)
	}
	if d2.DynamicTableLen() != 0 {
		t.Fatal("decoder d2 observed d1's state")
	}
	// Dynamic index 1 is valid on d1 (index 62) but out of range on d2.
	if _, err := d1.DecodeBlock([]byte{0xbe}); err != nil {
		t.Fatalf("d1 should resolve index 62: %v", err)
	}
	_, err := d2.DecodeBlock([]byte{0xbe})
	wantErrKind(t, err, KindIndexOutOfRange)

	// Poisoning d1 must leave an unrelated decoder fully usable.
	_, _ = d1.DecodeBlock([]byte{0x80})
	if !d1.Poisoned() {
		t.Fatal("d1 not poisoned after index-zero")
	}
	d3 := newTestDecoder(t, 4096)
	if d3.Poisoned() {
		t.Fatal("fresh decoder d3 born poisoned: state leaked")
	}
	if _, err := d3.DecodeBlock(block); err != nil {
		t.Fatalf("d3 unusable after d1 poisoned: %v", err)
	}
}

func TestHeaderListBudget(t *testing.T) {
	d := NewDecoder(DecoderOptions{
		MaxTableSize: 4096,
		Limits:       Limits{MaxStringLen: 1024, MaxHeaderBlockEmitted: 5, MaxHeaderFields: 100},
	})
	// One "x:12345" field emits 6 bytes > budget 5.
	_, err := d.DecodeBlock([]byte{0x40, 0x01, 'x', 0x05, '1', '2', '3', '4', '5'})
	wantErrKind(t, err, KindHeaderListTooLarge)
}

func TestPseudoHeaderValidation(t *testing.T) {
	d := NewDecoder(DecoderOptions{
		MaxTableSize: 4096, Limits: DefaultLimits(), ValidatePseudo: true,
	})
	// Regular header first, then :method via static index 2.
	_, err := d.DecodeBlock([]byte{0x40, 0x01, 'x', 0x01, 'y', 0x82})
	wantErrKind(t, err, KindIllegalPseudo)

	d2 := NewDecoder(DecoderOptions{
		MaxTableSize: 4096, Limits: DefaultLimits(), ValidatePseudo: true,
	})
	// Duplicate :method (index 2 twice).
	_, err = d2.DecodeBlock([]byte{0x82, 0x82})
	wantErrKind(t, err, KindIllegalPseudo)
}

type recordingObserver struct{ events []Event }

func (r *recordingObserver) OnEvent(ev Event) { r.events = append(r.events, ev) }

func TestObserverTrail(t *testing.T) {
	obs := &recordingObserver{}
	d := NewDecoder(DecoderOptions{MaxTableSize: 256, Limits: DefaultLimits(), Observer: obs})
	block := []byte{0x3f, 0x00, 0x82} // size update to 31, then :method GET
	if _, err := d.DecodeBlock(block); err != nil {
		t.Fatal(err)
	}
	if len(obs.events) < 3 {
		t.Fatalf("events = %v", obs.events)
	}
	if obs.events[0].Kind != EvBlockStart || obs.events[1].Kind != EvSizeUpdate {
		t.Fatalf("event order = %v, want block-start,size-update", obs.events[:2])
	}
	if obs.events[1].NewMax != 31 {
		t.Fatalf("size update new max = %d, want 31", obs.events[1].NewMax)
	}
}
