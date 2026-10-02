// Package compat contains the independent compatibility tests of the
// fixture. All expected frames here are hand-computed byte vectors written
// out literally in this file — none of them are produced by the codec or
// server under test, so a systematic error in the implementation cannot
// silently reproduce the reference answers. Tests only ever talk to
// 127.0.0.1 loopback listeners; no real industrial device is involved.
package compat

import (
	"bytes"
	"encoding/hex"
	"io"
	"net"
	"testing"
	"time"

	"mbfixture/internal/mbcodec"
	"mbfixture/internal/mblog"
	"mbfixture/internal/mbserver"
	"mbfixture/internal/mbstore"
)

// testLogger discards log output but keeps the code path exercised.
func testLogger(component string) *mblog.Logger {
	return mblog.New(io.Discard, component)
}

// startServer brings up a slave fixture on a loopback ephemeral port with
// the given register-space size and optional per-request delay.
func startServer(t *testing.T, size uint16, delay func(mbcodec.Header, []byte) time.Duration) string {
	t.Helper()
	store, err := mbstore.Open(":memory:", size)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	srv, err := mbserver.New([]uint8{1}, store, testLogger("server"))
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	srv.DelayFunc = delay
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go srv.Serve(ln)
	t.Cleanup(func() {
		srv.Close()
		store.Close()
	})
	return ln.Addr().String()
}

// dialRaw opens a raw TCP connection to the fixture.
func dialRaw(t *testing.T, addr string) net.Conn {
	t.Helper()
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { c.Close() })
	return c
}

// readRawFrame reads one ADU from the wire and returns the exact bytes.
func readRawFrame(t *testing.T, c net.Conn) []byte {
	t.Helper()
	c.SetReadDeadline(time.Now().Add(3 * time.Second))
	var hb [7]byte
	if _, err := io.ReadFull(c, hb[:]); err != nil {
		t.Fatalf("read header: %v", err)
	}
	length := int(hb[4])<<8 | int(hb[5])
	rest := make([]byte, length-1)
	if _, err := io.ReadFull(c, rest); err != nil {
		t.Fatalf("read pdu: %v", err)
	}
	return append(hb[:], rest...)
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("bad hex %q: %v", s, err)
	}
	return b
}

func assertFrame(t *testing.T, c net.Conn, wantHex string) {
	t.Helper()
	got := readRawFrame(t, c)
	want := mustHex(t, wantHex)
	if !bytes.Equal(got, want) {
		t.Fatalf("frame mismatch:\n got: %X\nwant: %X", got, want)
	}
}

// TestVectorWriteThenRead chains two hand-computed vectors: an FC16 write
// of [0x1234, 0xABCD] at address 0, then an FC03 read of the same range.
// Both expected responses are literal reference bytes.
func TestVectorWriteThenRead(t *testing.T) {
	addr := startServer(t, 16, nil)
	c := dialRaw(t, addr)

	// FC16: txid=2, unit=1, addr=0, qty=2, byteCount=4, data 12 34 AB CD.
	c.Write(mustHex(t, "00020000000B011000000002041234ABCD"))
	// Expect echo: txid=2, len=6, unit=1, func=0x10, addr=0, qty=2.
	assertFrame(t, c, "000200000006011000000002")

	// FC03: txid=1, unit=1, addr=0, qty=2.
	c.Write(mustHex(t, "000100000006010300000002"))
	// Expect: len=7, func=03, byteCount=4, data 12 34 AB CD.
	assertFrame(t, c, "0001000000070103041234ABCD")
}

// TestVectorExceptions checks the exception responses for the failure
// categories: unsupported function, address out of range, quantity out of
// range, quantity/byte-count mismatch, unknown unit id.
func TestVectorExceptions(t *testing.T) {
	addr := startServer(t, 16, nil)
	c := dialRaw(t, addr)

	cases := []struct {
		name string
		req  string
		resp string
	}{
		// Unsupported function 0x2B -> 0xAB, exception 01 ILLEGAL_FUNCTION.
		{"unsupported_func", "000300000002012B", "00030000000301AB01"},
		// Read addr=14 qty=4 with 16 registers -> 14+4=18 > 16, exc 02.
		{"read_addr_range", "0004000000060103000E0004", "000400000003018302"},
		// Read qty=0 -> exc 03 ILLEGAL_DATA_VALUE.
		{"read_qty_zero", "000500000006010300000000", "000500000003018303"},
		// Read qty=126 > 125 -> exc 03.
		{"read_qty_too_big", "00060000000601030000007E", "000600000003018303"},
		// FC16 qty=2 but byteCount=3 -> exc 03.
		{"write_qty_mismatch", "00070000000A01100000000203010203", "000700000003019003"},
		// Unknown unit id 9 -> exc 0B GATEWAY_TARGET_NOT_RESPONDING.
		{"unknown_unit", "000800000006090300000001", "00080000000309830B"},
	}
	for _, tc := range cases {
		c.Write(mustHex(t, tc.req))
		got := readRawFrame(t, c)
		want := mustHex(t, tc.resp)
		if !bytes.Equal(got, want) {
			t.Fatalf("%s:\n got: %X\nwant: %X", tc.name, got, want)
		}
	}
}

// TestHalfPacket sends a request in three fragments with pauses; the server
// must reassemble it and answer correctly.
func TestHalfPacket(t *testing.T) {
	addr := startServer(t, 16, nil)
	c := dialRaw(t, addr)
	full := mustHex(t, "000100000006010300000002")
	for _, cut := range [][]byte{full[:3], full[3:8], full[8:]} {
		if _, err := c.Write(cut); err != nil {
			t.Fatalf("write fragment: %v", err)
		}
		time.Sleep(30 * time.Millisecond)
	}
	// Registers are all zero in a fresh fixture.
	assertFrame(t, c, "00010000000701030400000000")
}

// TestStickyPacket writes two requests in a single TCP write; the server
// must emit two separate, correctly identified responses.
func TestStickyPacket(t *testing.T) {
	addr := startServer(t, 16, nil)
	c := dialRaw(t, addr)
	two := append(mustHex(t, "000100000006010300000001"),
		mustHex(t, "000200000006010300010001")...)
	if _, err := c.Write(two); err != nil {
		t.Fatalf("write: %v", err)
	}
	got := map[uint16][]byte{}
	for i := 0; i < 2; i++ {
		f := readRawFrame(t, c)
		txid := uint16(f[0])<<8 | uint16(f[1])
		got[txid] = f
	}
	if want := mustHex(t, "0001000000050103020000"); !bytes.Equal(got[1], want) {
		t.Fatalf("txid 1:\n got: %X\nwant: %X", got[1], want)
	}
	if want := mustHex(t, "0002000000050103020000"); !bytes.Equal(got[2], want) {
		t.Fatalf("txid 2:\n got: %X\nwant: %X", got[2], want)
	}
}

// TestOutOfOrderResponses delays the first request so the second one is
// answered first; both responses must still carry their own transaction ID
// and correct payloads.
func TestOutOfOrderResponses(t *testing.T) {
	delay := func(h mbcodec.Header, pdu []byte) time.Duration {
		if h.TxID == 1 {
			return 200 * time.Millisecond
		}
		return 0
	}
	addr := startServer(t, 16, delay)
	c := dialRaw(t, addr)
	// txid=1 reads addr 0 (delayed), txid=2 writes addr 4 (immediate).
	c.Write(mustHex(t, "000100000006010300000001"))
	c.Write(mustHex(t, "00020000000901100004000102BEEF"))

	first := readRawFrame(t, c)
	second := readRawFrame(t, c)
	// The write (txid=2) must arrive before the delayed read (txid=1).
	if txid := uint16(first[0])<<8 | uint16(first[1]); txid != 2 {
		t.Fatalf("first response txid=%d, want 2 (out-of-order expected)", txid)
	}
	if want := mustHex(t, "000200000006011000040001"); !bytes.Equal(first, want) {
		t.Fatalf("write response:\n got: %X\nwant: %X", first, want)
	}
	if want := mustHex(t, "0001000000050103020000"); !bytes.Equal(second, want) {
		t.Fatalf("read response:\n got: %X\nwant: %X", second, want)
	}
}

// TestMBAPViolationsCloseConnection sends frames with a non-zero protocol
// ID and with an out-of-range length field; the server must close the
// connection without emitting a response.
func TestMBAPViolationsCloseConnection(t *testing.T) {
	cases := []struct {
		name string
		req  string
	}{
		{"proto_id_nonzero", "000100010006010300000001"},
		{"length_too_small", "00010000000101"},
		{"length_too_large", "0001000000FF0103"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			addr := startServer(t, 16, nil)
			c := dialRaw(t, addr)
			c.Write(mustHex(t, tc.req))
			c.SetReadDeadline(time.Now().Add(2 * time.Second))
			one := make([]byte, 1)
			n, err := c.Read(one)
			if err == nil && n > 0 {
				t.Fatalf("server sent %d bytes instead of closing", n)
			}
		})
	}
}
