package protocol_test

import (
	"encoding/binary"
	"testing"
	"time"

	"ntpsim/internal/protocol"
)

// TestHandAssembledBytes decodes a packet assembled field-by-field by hand
// and checks every byte offset against the RFC 5905 layout.
func TestHandAssembledBytes(t *testing.T) {
	// Build a known reply: LI=0, VN=4, Mode=4 => first byte 00 100 100 = 0x24.
	// stratum=2, poll=6, precision=-20 (0xec).
	b := make([]byte, 48)
	b[0] = 0x24
	b[1] = 2
	b[2] = 6
	b[3] = 0xec
	binary.BigEndian.PutUint16(b[4:6], 1)      // root delay seconds
	binary.BigEndian.PutUint16(b[6:8], 0x8000) // root delay fraction .5
	binary.BigEndian.PutUint16(b[8:10], 0)
	binary.BigEndian.PutUint16(b[10:12], 0x4000) // dispersion .25
	binary.BigEndian.PutUint32(b[12:16], 0x0A000001)

	// t=2024-01-01T00:00:00Z as receive, +1s transmit. Origin left zero.
	t2 := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)
	t3 := t2.Add(time.Second)
	ts2 := protocol.TimestampFromTime(t2)
	ts3 := protocol.TimestampFromTime(t3)
	binary.BigEndian.PutUint32(b[32:36], ts2.Seconds())
	binary.BigEndian.PutUint32(b[36:40], ts2.Fraction())
	binary.BigEndian.PutUint32(b[40:44], ts3.Seconds())
	binary.BigEndian.PutUint32(b[44:48], ts3.Fraction())

	p, err := protocol.Decode(b)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	if p.LI != 0 || p.Version != 4 || p.Mode != protocol.ModeServer {
		t.Fatalf("first byte decoded wrong: LI=%d VN=%d mode=%d", p.LI, p.Version, p.Mode)
	}
	if p.Stratum != 2 || p.Poll != 6 || p.Precision != -20 {
		t.Fatalf("header fields wrong: stratum=%d poll=%d prec=%d", p.Stratum, p.Poll, p.Precision)
	}
	if got := p.RootDelay.Duration(); got != 1500*time.Millisecond {
		t.Fatalf("root delay = %v, want 1.5s", got)
	}
	if got := p.RootDispersion.Duration(); got != 250*time.Millisecond {
		t.Fatalf("root dispersion = %v, want 250ms", got)
	}
	if p.ReferenceID != 0x0A000001 {
		t.Fatalf("ref id = %#x", p.ReferenceID)
	}
	if !p.ReceiveTime.Time().Equal(t2) {
		t.Fatalf("receive = %v want %v", p.ReceiveTime.Time(), t2)
	}
	if !p.TransmitTime.Time().Equal(t3) {
		t.Fatalf("transmit = %v want %v", p.TransmitTime.Time(), t3)
	}
	if p.OriginTime != 0 {
		t.Fatalf("origin should be zero, got %d", p.OriginTime)
	}
}

// TestEncodeRoundTrip checks that Encode reproduces the exact input bytes.
func TestEncodeRoundTrip(t *testing.T) {
	b := make([]byte, 48)
	b[0] = 0x24
	b[1] = 3
	b[2] = 7
	b[3] = 0xec
	binary.BigEndian.PutUint32(b[12:16], 0x7F000001)
	now := time.Date(2025, 6, 1, 12, 0, 0, 250_000_000, time.UTC)
	ts := protocol.TimestampFromTime(now)
	binary.BigEndian.PutUint32(b[40:44], ts.Seconds())
	binary.BigEndian.PutUint32(b[44:48], ts.Fraction())

	p, err := protocol.Decode(b)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	out := p.Encode()
	if string(out) != string(b) {
		t.Fatalf("round-trip mismatch:\n in=% x\nout=% x", b, out)
	}
	// 250ms is exactly representable: fraction = 1/4 of 2^32 = 0x40000000.
	if got := p.TransmitTime.Fraction(); got != 0x40000000 {
		t.Fatalf("250ms fraction = %#x want 0x40000000", got)
	}
	if got := p.TransmitTime.Time(); got.Nanosecond() != 250_000_000 {
		t.Fatalf("nanos = %d", got.Nanosecond())
	}
}

func TestDecodeErrors(t *testing.T) {
	if _, err := protocol.Decode(make([]byte, 47)); err == nil {
		t.Fatal("short packet must error")
	}
	// Mode 3 (client) is not a valid server response.
	b := make([]byte, 48)
	b[0] = (4 << 3) | 3 // VN4 client
	if _, err := protocol.Decode(b); err == nil {
		t.Fatal("client-mode packet must be rejected")
	}
	// Version 7 invalid.
	b[0] = (7 << 3) | 4
	if _, err := protocol.Decode(b); err == nil {
		t.Fatal("VN7 packet must be rejected")
	}
}

func TestClientRequestShape(t *testing.T) {
	now := time.Date(2024, 1, 2, 3, 4, 5, 0, time.UTC)
	p := protocol.NewClientRequest(now)
	if p.Version != 4 || p.Mode != protocol.ModeClient {
		t.Fatalf("want VN4 client, got VN%d mode%d", p.Version, p.Mode)
	}
	if len(p.Encode()) != 48 {
		t.Fatalf("encoded length %d, want 48", len(p.Encode()))
	}
	if !p.TransmitTime.Time().Equal(now) {
		t.Fatalf("transmit %v != %v", p.TransmitTime.Time(), now)
	}
}

func TestKissCode(t *testing.T) {
	p := &protocol.Packet{Stratum: 0}
	p.ReferenceID = uint32('D')<<24 | uint32('E')<<16 | uint32('N')<<8 | uint32('Y')
	if !p.IsKissOfDeath() || p.KissCode() != "DENY" {
		t.Fatalf("kod=%v code=%q", p.IsKissOfDeath(), p.KissCode())
	}
}
