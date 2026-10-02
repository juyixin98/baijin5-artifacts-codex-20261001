package frame

import (
	"bytes"
	"errors"
	"testing"
)

func TestFrameRoundTrip(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)
	payload := []byte{0xde, 0xad, 0xbe, 0xef}
	if err := w.Headers(0x7fffffff, FlagEndHeaders|FlagEndStream, payload); err != nil {
		t.Fatal(err)
	}
	r := NewReader(&buf, MaxFrameSizeDefault)
	h, got, err := r.ReadFrame()
	if err != nil {
		t.Fatal(err)
	}
	if h.Type != TypeHeaders {
		t.Errorf("type = %d, want %d", h.Type, TypeHeaders)
	}
	if !h.EndHeaders() || !h.EndStream() {
		t.Errorf("flags = %02x, want END_HEADERS|END_STREAM", h.Flags)
	}
	if h.Stream != 0x7fffffff {
		t.Errorf("stream = %d, want max masked id", h.Stream)
	}
	if !bytes.Equal(got, payload) {
		t.Errorf("payload = %x, want %x", got, payload)
	}
}

func TestFrameStreamIDMasked(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)
	// Set the reserved high bit; it must be cleared on the wire.
	if err := w.Data(0x80000003, 0, []byte("x")); err != nil {
		t.Fatal(err)
	}
	raw := buf.Bytes()
	if raw[5]&0x80 != 0 {
		t.Fatalf("reserved bit not cleared: %08b", raw[5])
	}
	r := NewReader(&buf, MaxFrameSizeDefault)
	h, _, err := r.ReadFrame()
	if err != nil {
		t.Fatal(err)
	}
	if h.Stream != 3 {
		t.Fatalf("stream = %d, want 3", h.Stream)
	}
}

func TestFrameTooLargeRejected(t *testing.T) {
	// Build a frame claiming 17000 payload bytes but cap reads at 16384.
	var buf bytes.Buffer
	w := NewWriter(&buf)
	big := make([]byte, 17000)
	if err := w.GoAway(0, 0, big); err != nil {
		t.Fatal(err)
	}
	r := NewReader(bytes.NewReader(buf.Bytes()), MaxFrameSizeMin)
	if _, _, err := r.ReadFrame(); !errors.Is(err, ErrFrameTooLarge) {
		t.Fatalf("want ErrFrameTooLarge, got %v", err)
	}
}

func TestSettingsAndAckEncoding(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)
	if err := w.Settings(false, [2]uint32{1, 4096}, [2]uint32{3, 100}); err != nil {
		t.Fatal(err)
	}
	raw := buf.Bytes()
	if raw[3] != TypeSettings {
		t.Fatalf("type byte = %d", raw[3])
	}
	if raw[4] != 0 {
		t.Fatalf("non-ack settings flags = %d", raw[4])
	}
	if len(raw) != 9+12 {
		t.Fatalf("frame len = %d, want 21", len(raw))
	}

	buf.Reset()
	if err := w.Settings(true); err != nil {
		t.Fatal(err)
	}
	if buf.Bytes()[4] != FlagAck {
		t.Fatal("ACK frame missing ACK flag")
	}
}

func TestGoAwayPayloadShape(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)
	if err := w.GoAway(7, 0x9, []byte("boom")); err != nil {
		t.Fatal(err)
	}
	r := NewReader(&buf, MaxFrameSizeDefault)
	h, p, err := r.ReadFrame()
	if err != nil {
		t.Fatal(err)
	}
	if h.Type != TypeGoAway || h.Stream != 0 {
		t.Fatalf("GOAWAY framing wrong: type=%d stream=%d", h.Type, h.Stream)
	}
	if got := uint32(p[0])<<24 | uint32(p[1])<<16 | uint32(p[2])<<8 | uint32(p[3]); got != 7 {
		t.Errorf("last stream = %d", got)
	}
	if got := uint32(p[4])<<24 | uint32(p[5])<<16 | uint32(p[6])<<8 | uint32(p[7]); got != 0x9 {
		t.Errorf("error code = %d", got)
	}
	if string(p[8:]) != "boom" {
		t.Errorf("debug = %q", p[8:])
	}
}
