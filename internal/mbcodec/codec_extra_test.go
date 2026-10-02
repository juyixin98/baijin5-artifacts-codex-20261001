package mbcodec

import (
	"bytes"
	"testing"
)

func TestWriteFrameRoundTrip(t *testing.T) {
	var buf bytes.Buffer
	pdu := EncodeReadHoldingRequest(0x0010, 3)
	h := Header{TxID: 0x00AA, UnitID: 4}
	if err := WriteFrame(&buf, h, pdu); err != nil {
		t.Fatalf("write: %v", err)
	}
	f, err := ReadFrame(&buf)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if f.Header.TxID != h.TxID || f.Header.UnitID != h.UnitID ||
		f.Header.Length != uint16(1+len(pdu)) {
		t.Fatalf("header mismatch: %+v", f.Header)
	}
	if !bytes.Equal(f.PDU, pdu) {
		t.Fatalf("pdu mismatch: %X vs %X", f.PDU, pdu)
	}
}

func TestWriteMultipleRoundTrip(t *testing.T) {
	values := []uint16{0, 1, 0xFFFF, 0x00FF}
	pdu := EncodeWriteMultipleRequest(0x0100, values)
	addr, got, err := DecodeWriteMultipleRequest(pdu)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if addr != 0x0100 || !equalU16(got, values) {
		t.Fatalf("got addr=%d values=%v", addr, got)
	}

	resp := EncodeWriteMultipleResponse(0x0100, 4)
	rAddr, rQty, err := DecodeWriteMultipleResponse(resp)
	if err != nil || rAddr != 0x0100 || rQty != 4 {
		t.Fatalf("response decode: addr=%d qty=%d err=%v", rAddr, rQty, err)
	}
}

func TestReadHoldingRoundTrip(t *testing.T) {
	values := []uint16{0x1234, 0xABCD}
	pdu := EncodeReadHoldingResponse(values)
	got, err := DecodeReadHoldingResponse(pdu, 2)
	if err != nil || !equalU16(got, values) {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestDecodeRejectsTruncation(t *testing.T) {
	if _, err := DecodeHeader([]byte{0, 1, 0}); err == nil {
		t.Fatal("short header accepted")
	}
	if _, _, err := DecodeReadHoldingRequest([]byte{0x03, 0x00}); err == nil {
		t.Fatal("short fc03 request accepted")
	}
	if _, _, err := DecodeWriteMultipleResponse([]byte{0x10, 0x00}); err == nil {
		t.Fatal("short fc16 response accepted")
	}
	if _, err := DecodeReadHoldingResponse([]byte{0x03, 0x04, 0x00}, 2); err == nil {
		t.Fatal("truncated fc03 response accepted")
	}
}

func equalU16(a, b []uint16) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
