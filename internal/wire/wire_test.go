package wire_test

import (
	"bytes"
	"encoding/hex"
	"errors"
	"testing"

	"coaplab/internal/wire"
)

// RFC 7252 Appendix A, Figure 16:
// CON GET /temperature, MID 0x7d34, no token -> exactly 16 bytes.
func TestRFC7252_Figure16_GETBytes(t *testing.T) {
	m := &wire.Message{
		Type: wire.CON, Code: wire.GET, MID: 0x7d34,
		Options: []wire.Option{{Number: wire.OpURIPath, Value: []byte("temperature")}},
	}
	got, err := m.Marshal()
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	// Option byte 0xbb = delta 11 / length 11 (Uri-Path, "temperature").
	want := "40017d34bb74656d7065726174757265"
	if h := hex.EncodeToString(got); h != want {
		t.Fatalf("Figure 16 request bytes:\n got %s\nwant %s", h, want)
	}
	if len(got) != 16 {
		t.Fatalf("RFC states 16 bytes, got %d", len(got))
	}
}

// RFC 7252 Figure 16 response: ACK 2.05 Content MID 0x7d34 payload "22.3 C".
func TestRFC7252_Figure16_ResponseBytes(t *testing.T) {
	m := &wire.Message{
		Type: wire.ACK, Code: wire.Content, MID: 0x7d34,
		Payload: []byte("22.3 C"),
	}
	got, err := m.Marshal()
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	// Ver=1 T=ACK(2) => 0x60; 2.05=69=0x45; then payload marker.
	want := "60457d34ff32322e332043"
	if h := hex.EncodeToString(got); h != want {
		t.Fatalf("Figure 16 response bytes:\n got %s\nwant %s", h, want)
	}
}

// Figure 17 adds a one-byte Token 0x20, making both messages one byte longer.
func TestRFC7252_Figure17_TokenEcho(t *testing.T) {
	req := &wire.Message{
		Type: wire.CON, Code: wire.GET, MID: 0x7d35, Token: []byte{0x20},
		Options: []wire.Option{{Number: wire.OpURIPath, Value: []byte("temperature")}},
	}
	got, _ := req.Marshal()
	want := "41017d3520" + "bb74656d7065726174757265"
	if hex.EncodeToString(got) != want {
		t.Fatalf("Figure 17 request:\n got %x\nwant %s", got, want)
	}
	if len(got) != 17 {
		t.Fatalf("RFC states 17 bytes, got %d", len(got))
	}
}

func TestParse_Figure16Vector(t *testing.T) {
	raw, _ := hex.DecodeString("40017d34bb74656d7065726174757265")
	m, err := wire.Parse(raw)
	if err != nil {
		t.Fatalf("Parse RFC vector: %v", err)
	}
	if m.Type != wire.CON || m.Code != wire.GET || m.MID != 0x7d34 || len(m.Token) != 0 {
		t.Fatalf("header decoded wrong: %+v", m)
	}
	if got := m.Path(); len(got) != 1 || got[0] != "temperature" {
		t.Fatalf("Uri-Path = %v", got)
	}
}

// RFC 7959 §3: Block2 option value 33 renders 2:2/0/32; value 59 -> 1:3/1/128.
func TestRFC7959_BlockOptionVectors(t *testing.T) {
	b33 := wire.Block{NUM: 2, M: false, SZX: 1} // 2<<4|0|1 = 33
	if v := b33.Encode(); len(v) != 1 || v[0] != 33 {
		t.Fatalf("33 vector = %v", v)
	}
	b59 := wire.Block{NUM: 3, M: true, SZX: 3} // 3<<4|8|3 = 59
	if v := b59.Encode(); len(v) != 1 || v[0] != 59 {
		t.Fatalf("59 vector = %v", v)
	}
	parsed, err := wire.DecodeBlock([]byte{33})
	if err != nil || parsed.NUM != 2 || parsed.M || parsed.SZX != 1 || parsed.Size() != 32 {
		t.Fatalf("decode 33 = %+v err=%v", parsed, err)
	}
	parsed59, err := wire.DecodeBlock([]byte{59})
	if err != nil || parsed59.NUM != 3 || !parsed59.M || parsed59.SZX != 3 || parsed59.Size() != 128 {
		t.Fatalf("decode 59 = %+v err=%v", parsed59, err)
	}
	// NUM=0/M=0/SZX=0 is the zero-length uint form.
	if v := (wire.Block{}).Encode(); len(v) != 0 {
		t.Fatalf("zero block must be 0 bytes, got %v", v)
	}
	b0, err := wire.DecodeBlock(nil)
	if err != nil || b0.NUM != 0 || b0.M || b0.SZX != 0 || b0.Size() != 16 {
		t.Fatalf("decode empty block value = %+v err=%v", b0, err)
	}
}

func TestBlockOffset(t *testing.T) {
	cases := []struct {
		b    wire.Block
		off  int64
		size int
	}{
		{wire.Block{NUM: 0, SZX: 0}, 0, 16},
		{wire.Block{NUM: 2, SZX: 1}, 64, 32},
		{wire.Block{NUM: 3, SZX: 6}, 3072, 1024},
	}
	for _, c := range cases {
		if c.b.Offset() != c.off {
			t.Errorf("NUM=%d SZX=%d offset = %d, want %d", c.b.NUM, c.b.SZX, c.b.Offset(), c.off)
		}
		if c.b.Size() != c.size {
			t.Errorf("NUM=%d SZX=%d size = %d, want %d", c.b.NUM, c.b.SZX, c.b.Size(), c.size)
		}
	}
}

// RFC 7959 §2.2: SZX 7 (2048) is reserved. The datagram still PARSES
// (message layer valid), but decoding the Block option yields an error so
// the handler answers 4.00 Bad Request rather than processing it.
func TestReservedSZX7Rejected(t *testing.T) {
	// CON PUT MID 0x1234; Block1(27)= extended delta 0xd1 0x0e, value 0x0f
	// (NUM=0, M=1, SZX=7).
	raw := []byte{0x40, 0x03, 0x12, 0x34, 0xd1, 0x0e, 0x0f}
	msg, err := wire.Parse(raw)
	if err != nil {
		t.Fatalf("SZX=7 must still parse as a datagram, got %v", err)
	}
	b, has, berr := msg.Block1()
	if !has || berr == nil {
		t.Fatalf("Block1() must surface SZX=7 as an error, got block=%+v err=%v", b, berr)
	}
	var pe *wire.ParseError
	if !errors.As(berr, &pe) {
		t.Fatalf("want *ParseError, got %T: %v", berr, berr)
	}
	if _, err := wire.DecodeBlock([]byte{0x0f}); err == nil {
		t.Fatal("DecodeBlock must reject SZX 7")
	}
}

func TestRoundTrip(t *testing.T) {
	orig := &wire.Message{
		Type: wire.NON, Code: wire.POST, MID: 0xbeef, Token: []byte{1, 2, 3, 4, 5, 6, 7, 8},
		Options: []wire.Option{
			{Number: wire.OpURIPath, Value: []byte("a")},
			{Number: wire.OpURIPath, Value: []byte("b")}, // repeated option, delta 0
			{Number: wire.OpContentFormat, Value: wire.EncodeUint(50)},
			{Number: 300, Value: []byte("ext-delta")}, // delta 269+31
		},
		Payload: []byte("p"),
	}
	raw, err := orig.Marshal()
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	got, err := wire.Parse(raw)
	if err != nil {
		t.Fatalf("Parse: %v (%x)", err, raw)
	}
	if got.Type != orig.Type || got.Code != orig.Code || got.MID != orig.MID ||
		!bytes.Equal(got.Token, orig.Token) || !bytes.Equal(got.Payload, orig.Payload) {
		t.Fatalf("round trip header/payload mismatch: %+v", got)
	}
	if len(got.Options) != 4 {
		t.Fatalf("options = %d", len(got.Options))
	}
	if p := got.Path(); len(p) != 2 || p[0] != "a" || p[1] != "b" {
		t.Fatalf("repeated Uri-Path lost: %v", p)
	}
}

func TestParseFailures(t *testing.T) {
	bad := map[string][]byte{
		"short":                {0x40, 0x01},
		"bad version":          {0x00, 0x01, 0x00, 0x00},
		"TKL overrun":          {0x48, 0x01, 0x00, 0x00, 0x01},
		"reserved len nibble":  {0x40, 0x01, 0x00, 0x00, 0x1f},
		"option value overrun": {0x40, 0x01, 0x00, 0x00, 0xb1},
		"first option delta 0": {0x40, 0x01, 0x00, 0x00, 0x01, 0x41},
		"empty with payload":   {0x40, 0x00, 0x00, 0x00, 0xff, 'x'},
		"RST not empty":        {0x70, 0x05, 0x00, 0x00},
		// Block1(27) via ext delta (d1 0e), then a second Block1 (delta 0).
		"repeated Block1": {0x40, 0x03, 0x00, 0x01, 0xd1, 0x0e, 0x00, 0x01, 0x00},
	}
	for name, dgram := range bad {
		t.Run(name, func(t *testing.T) {
			if _, err := wire.Parse(dgram); err == nil {
				t.Fatalf("%s: expected parse error", name)
			}
		})
	}
}

func TestTokenTooLong(t *testing.T) {
	m := &wire.Message{Type: wire.CON, Code: wire.GET, MID: 1, Token: make([]byte, 9)}
	if _, err := m.Marshal(); !errors.Is(err, wire.ErrTokenTooLong) {
		t.Fatalf("want ErrTokenTooLong, got %v", err)
	}
}

func TestEmptyACKRST(t *testing.T) {
	ack := wire.EmptyACK(0x1111)
	raw, err := ack.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	want, _ := hex.DecodeString("60001111")
	if !bytes.Equal(raw, want) {
		t.Fatalf("empty ACK = %x, want %x", raw, want)
	}
	rst := wire.EmptyRST(0x2222)
	raw2, _ := rst.Marshal()
	want2, _ := hex.DecodeString("70002222")
	if !bytes.Equal(raw2, want2) {
		t.Fatalf("RST = %x, want %x", raw2, want2)
	}
}

func buildBlock1Datagram(t *testing.T, mid uint16, b wire.Block, payload []byte) []byte {
	t.Helper()
	m := &wire.Message{
		Type: wire.CON, Code: wire.PUT, MID: mid,
		Options: []wire.Option{{Number: wire.OpBlock1, Value: b.Encode()}},
		Payload: payload,
	}
	raw, err := m.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
