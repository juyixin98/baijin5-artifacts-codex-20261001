package compat_test

import (
	"bytes"
	"encoding/hex"
	"testing"

	"modbusfixture/vectors"
)

// TestGoldenWireVectors sends the hand-computed byte sequences verbatim
// over a real loopback TCP connection and requires the fixture to return
// the exact hand-computed response bytes. The fixture never sees these
// expected bytes: they live in the dependency-free vectors module.
func TestGoldenWireVectors(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	for _, g := range vectors.Golden {
		if g.ReqHex == "" || g.RespHex == "" {
			continue
		}
		t.Run(g.Name, func(t *testing.T) {
			c := h.dial(t)
			defer c.Close()

			req := vectors.MustHex(g.ReqHex)
			want := vectors.MustHex(g.RespHex)
			writeFull(t, c, req, 0)
			got := readExactFrame(t, c)
			if !bytes.Equal(got, want) {
				t.Fatalf("wire mismatch for %s (%s):\n got %x\nwant %x",
					g.Name, g.Desc, got, want)
			}
			// Identity fields specifically: transaction id and unit id.
			if got[0] != req[0] || got[1] != req[1] {
				t.Fatalf("transaction id not echoed: got %02x%02x, want %02x%02x",
					got[0], got[1], req[0], req[1])
			}
			if got[6] != req[6] {
				t.Fatalf("unit id not echoed: got %02x, want %02x",
					got[6], req[6])
			}
		})
	}
}

// TestGoldenVectorWithHalfPacket splits each request at a different byte
// boundary: the server must buffer and still answer the exact golden bytes.
func TestGoldenVectorWithHalfPacket(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	req := vectors.MustHex(vectors.Golden[0].ReqHex)
	want := vectors.MustHex(vectors.Golden[0].RespHex)

	for _, split := range []int{1, 3, 6, 7, 8, 11} {
		name := "split_after_" + itoa(split)
		t.Run(name, func(t *testing.T) {
			c := h.dial(t)
			defer c.Close()
			writeFull(t, c, req, split)
			got := readExactFrame(t, c)
			if !bytes.Equal(got, want) {
				t.Fatalf("split=%d:\n got %x\nwant %x", split, got, want)
			}
		})
	}
}

// TestStickyPackets glues two requests into one TCP segment. Both
// responses must come back as correctly bounded frames with each
// transaction id attached to its own data (no cross-request mixing).
func TestStickyPackets(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	f1 := vectors.MustHex(vectors.Golden[0].ReqHex) // txn 0x1234, unit 0x05
	f2 := vectors.MustHex(vectors.Golden[1].ReqHex) // txn 0x0001, unit 0x01
	want1 := vectors.MustHex(vectors.Golden[0].RespHex)
	want2 := vectors.MustHex(vectors.Golden[1].RespHex)

	c := h.dial(t)
	defer c.Close()

	glued := make([]byte, 0, len(f1)+len(f2))
	glued = append(glued, f1...)
	glued = append(glued, f2...)
	if _, err := c.Write(glued); err != nil {
		t.Fatalf("write glued: %v", err)
	}

	got := make(map[uint16][]byte)
	for i := 0; i < 2; i++ {
		frame := readExactFrame(t, c)
		txn := uint16(frame[0])<<8 | uint16(frame[1])
		if _, dup := got[txn]; dup {
			t.Fatalf("duplicate response for txn %04x", txn)
		}
		got[txn] = frame
	}
	if !bytes.Equal(got[0x1234], want1) {
		t.Fatalf("txn 1234:\n got %x\nwant %x", got[0x1234], want1)
	}
	if !bytes.Equal(got[0x0001], want2) {
		t.Fatalf("txn 0001:\n got %x\nwant %x", got[0x0001], want2)
	}
}

// TestStickyThenHalfOnOneConnection combines three requests: two glued,
// then a third arriving as a half packet. All three must stay distinct.
func TestStickyThenHalfOnOneConnection(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	f1 := vectors.MustHex(vectors.Golden[0].ReqHex)
	f2 := vectors.MustHex(vectors.Golden[1].ReqHex)
	// third independent request: txn 0x0002 unit 0x01 read addr 99 qty 1
	f3 := vectors.MustHex(vectors.Golden[2].ReqHex)

	c := h.dial(t)
	defer c.Close()

	buf := append(append([]byte{}, f1...), f2...)
	if _, err := c.Write(buf); err != nil {
		t.Fatalf("write: %v", err)
	}
	readExactFrame(t, c)
	readExactFrame(t, c)

	// Half packet: 4 bytes then the rest.
	writeFull(t, c, f3, 4)
	frame := readExactFrame(t, c)
	if hex.EncodeToString(frame[:7])[:4] != "0002" {
		t.Fatalf("third frame txn = %x, want 0002", frame[:2])
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
