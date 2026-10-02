package compat_test

import (
	"bytes"
	"encoding/binary"
	"testing"

	"modbusfixture/vectors"
)

// TestPDUExceptions sends MBAP-well-formed but illegal PDUs and requires
// Modbus EXCEPTION responses (never ordinary data), matching the
// hand-computed response bytes in the independent vectors module.
func TestPDUExceptions(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	for _, c := range vectors.PDUMalformed {
		t.Run(c.Name, func(t *testing.T) {
			conn := h.dial(t)
			defer conn.Close()

			writeFull(t, conn, vectors.MustHex(c.ReqHex), 0)
			got := readExactFrame(t, conn)
			want := vectors.MustHex(c.RespHex)
			if !bytes.Equal(got, want) {
				t.Fatalf("%s (%s)\n got %x\nwant %x",
					c.Name, c.Desc, got, want)
			}
			// Exception bit must be set on the function code.
			if got[7]&0x80 == 0 {
				t.Fatalf("response is ordinary data, not exception: %x", got[7])
			}
			if got[8] != c.Exception {
				t.Fatalf("exception code = %02x, want %02x", got[8], c.Exception)
			}
			// Transaction id and unit id are still echoed exactly.
			if got[0] != vectors.MustHex(c.ReqHex)[0] ||
				got[1] != vectors.MustHex(c.ReqHex)[1] {
				t.Fatalf("txn id not echoed on exception response")
			}
			if got[6] != vectors.MustHex(c.ReqHex)[6] {
				t.Fatalf("unit id not echoed on exception response")
			}
		})
	}
}

// TestUnboundUnitIDRejected: the MBAP unit id is validated independently.
// A unit with no register bank must yield exception 0x0B (gateway target),
// even though the PDU itself (FC03) is perfectly legal.
func TestUnboundUnitIDRejected(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	conn := h.dial(t)
	defer conn.Close()

	// unit 0x07 is not bound in the harness; legal FC03 read.
	req := vectors.ReadHoldingReq(0x7777, 0x07, 0, 1)
	writeFull(t, conn, req, 0)
	got := readExactFrame(t, conn)

	// Unbound unit maps to gateway target 0x0B.
	want := vectors.ExceptionResp(0x7777, 0x07, 0x03, 0x0B)
	if !bytes.Equal(got, want) {
		t.Fatalf("unbound unit:\n got %x\nwant %x", got, want)
	}
}

// TestBoundaryAddresses checks the exact in/out-of-bank boundary for both
// function codes at the 125-register unit 1 bank (addresses 0..124).
func TestBoundaryAddresses(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	cases := []struct {
		name      string
		reqHex    string
		exception byte
	}{
		// FC03 addr=124 qty=1: last legal register -> normal response.
		{"fc03_last_register_ok", "0001000000060103007c0001", 0},
		// FC03 addr=125 qty=1: first address past bank -> 0x02.
		{"fc03_first_past_bank", "0001000000060103007d0001", 0x02},
		// FC16 addr=124 qty=1: last legal register -> normal.
		{"fc16_last_register_ok", "0001000000090110007c000102abcd", 0},
		// FC16 addr=124 qty=2: one register past bank -> 0x02.
		{"fc16_span_past_bank", "00010000000b0110007c00020411112222", 0x02},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			conn := h.dial(t)
			defer conn.Close()
			writeFull(t, conn, vectors.MustHex(tc.reqHex), 0)
			got := readExactFrame(t, conn)
			if tc.exception == 0 {
				if got[7]&0x80 != 0 {
					t.Fatalf("expected success, got exception %02x", got[8])
				}
			} else {
				if got[7]&0x80 == 0 {
					t.Fatalf("expected exception, got data: %x", got)
				}
				if got[8] != tc.exception {
					t.Fatalf("exception = %02x, want %02x", got[8], tc.exception)
				}
			}
		})
	}
}

// TestQuantityLimits pins the FC03 125 and FC16 123 register limits.
func TestQuantityLimits(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	t.Run("fc03_qty_125_ok", func(t *testing.T) {
		conn := h.dial(t)
		defer conn.Close()
		writeFull(t, conn, vectors.ReadHoldingReq(1, 1, 0, 125), 0)
		got := readExactFrame(t, conn)
		if got[7]&0x80 != 0 {
			t.Fatalf("qty 125 rejected: %x", got[8])
		}
		if got[8] != 250 { // byte count = 250
			t.Fatalf("byte count = %d, want 250", got[8])
		}
	})

	t.Run("fc16_qty_123_ok", func(t *testing.T) {
		conn := h.dial(t)
		defer conn.Close()
		vals := make([]uint16, 123)
		for i := range vals {
			vals[i] = uint16(i)
		}
		writeFull(t, conn, vectors.WriteMultiReq(1, 1, 0, vals, -1), 0)
		got := readExactFrame(t, conn)
		if got[7]&0x80 != 0 {
			t.Fatalf("qty 123 rejected: exception %02x", got[8])
		}
	})

	t.Run("fc16_qty_124_is_unframeable", func(t *testing.T) {
		conn := h.dial(t)
		defer conn.Close()
		// qty=124 would make MBAP length = 1+6+248 = 255, one over the
		// Modbus TCP ceiling of 254: such a request cannot legally be
		// framed. The server must reject it at framing level (length_too_large)
		// and close the connection rather than answer with data.
		pdu := make([]byte, 0, 6+2*124)
		pdu = append(pdu, 0x10, 0x00, 0x00, 0x00, 0x7C, 0xF8) // qty 124, bc 248
		for i := 0; i < 124; i++ {
			pdu = append(pdu, 0x00, 0x00)
		}
		over := vectors.Frame(1, 1, pdu...)
		if binary.BigEndian.Uint16(over[4:6]) != 255 {
			t.Fatalf("test setup: length = %d, want 255",
				binary.BigEndian.Uint16(over[4:6]))
		}
		writeFull(t, conn, over, 0)
		if _, err := readFrameOrClose(t, conn); err == nil {
			t.Fatal("server answered an unframeable request; want connection close")
		}
	})
}
