package vectors

import (
	"encoding/hex"
	"testing"
)

// TestMustHexRoundTrips guarantees the test oracle's own hex decoder
// produces the exact nibbles written in the hand-computed literals.
func TestMustHexRoundTrips(t *testing.T) {
	for _, g := range Golden {
		for _, s := range []string{g.ReqHex, g.RespHex} {
			if s == "" {
				continue
			}
			b := MustHex(s)
			if hex.EncodeToString(b) != s {
				t.Fatalf("MustHex(%q) round trip = %x", s, b)
			}
		}
	}
}

func TestMustHexRejectsBadInput(t *testing.T) {
	for _, bad := range []string{"abc", "0xZZ", "123"} {
		func() {
			defer func() {
				if r := recover(); r == nil {
					t.Fatalf("MustHex(%q) should panic", bad)
				}
			}()
			_ = MustHex(bad)
		}()
	}
}

// TestGoldenMBAPConsistency independently re-parses each golden literal and
// checks the MBAP length field equals 1 + PDU bytes, protocol id is zero and
// the function codes are the documented ones.
func TestGoldenMBAPConsistency(t *testing.T) {
	for _, g := range Golden {
		for _, label := range []string{"req", "resp"} {
			s := g.ReqHex
			if label == "resp" {
				s = g.RespHex
			}
			if s == "" {
				continue
			}
			b := MustHex(s)
			if len(b) < 8 {
				t.Fatalf("%s/%s shorter than MBAP+FC", g.Name, label)
			}
			if b[2] != 0 || b[3] != 0 {
				t.Fatalf("%s/%s protocol id = %02x%02x", g.Name, label, b[2], b[3])
			}
			declared := int(b[4])<<8 | int(b[5])
			if declared != len(b)-6 {
				t.Fatalf("%s/%s length field %d != 1+pdu %d (frame %d)",
					g.Name, label, declared, len(b)-6, len(b))
			}
		}
	}
}

// TestBuilderLengthBytes verifies the independent builders emit the exact
// length values a hand calculation would produce.
func TestBuilderLengthBytes(t *testing.T) {
	rd := ReadHoldingReq(0x1234, 0x05, 0, 2)
	if rd[4] != 0x00 || rd[5] != 0x06 {
		t.Fatalf("read request length = %02x%02x, want 0006", rd[4], rd[5])
	}
	wr := WriteMultiReq(1, 1, 10, []uint16{1, 2}, -1)
	// 1 unit + 6 FC16 header + 4 data = 11 (0x0B)
	if wr[4] != 0x00 || wr[5] != 0x0B {
		t.Fatalf("write request length = %02x%02x, want 000b", wr[4], wr[5])
	}
	er := ExceptionResp(0xABCD, 0x05, 0x03, 0x02)
	if er[4] != 0x00 || er[5] != 0x03 {
		t.Fatalf("exception length = %02x%02x, want 0003", er[4], er[5])
	}
	if er[7] != 0x83 || er[8] != 0x02 {
		t.Fatalf("exception pdu = %02x%02x, want 8302", er[7], er[8])
	}
}
