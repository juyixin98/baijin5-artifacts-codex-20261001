package mbcodec

import (
	"bytes"
	"errors"
	"io"
	"testing"
)

// headerBytes returns the 7-byte wire form of h as a slice.
func headerBytes(h Header) []byte {
	b := EncodeHeader(h)
	return b[:]
}

func TestHeaderRoundTrip(t *testing.T) {
	h := Header{TxID: 0x1234, ProtoID: 0, Length: 6, UnitID: 0x11}
	got, err := DecodeHeader(headerBytes(h))
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got != h {
		t.Fatalf("round trip mismatch: got %+v want %+v", got, h)
	}
}

func TestDecodeHeaderValidation(t *testing.T) {
	base := EncodeHeader(Header{TxID: 1, ProtoID: 0, Length: 6, UnitID: 1})

	badProto := base
	badProto[3] = 1 // protocol id = 1
	if _, err := DecodeHeader(badProto[:]); !errors.Is(err, ErrProtocolID) {
		t.Fatalf("proto id: got %v, want ErrProtocolID", err)
	}

	zeroLen := base
	zeroLen[4], zeroLen[5] = 0, 0
	if _, err := DecodeHeader(zeroLen[:]); !errors.Is(err, ErrLengthRange) {
		t.Fatalf("length 0: got %v, want ErrLengthRange", err)
	}

	hugeLen := base
	hugeLen[4], hugeLen[5] = 0x01, 0x00 // 256 > MaxMBAPLength
	if _, err := DecodeHeader(hugeLen[:]); !errors.Is(err, ErrLengthRange) {
		t.Fatalf("length 256: got %v, want ErrLengthRange", err)
	}
}

// TestReadFrameFragmented feeds a frame byte-by-byte; ReadFrame must
// reassemble it exactly (half-packet handling at the codec level).
func TestReadFrameFragmented(t *testing.T) {
	full := append(headerBytes(Header{TxID: 7, Length: 6, UnitID: 1}),
		EncodeReadHoldingRequest(0, 2)...)
	pr, pw := io.Pipe()
	go func() {
		for _, b := range full {
			pw.Write([]byte{b})
		}
		pw.Close()
	}()
	f, err := ReadFrame(pr)
	if err != nil {
		t.Fatalf("ReadFrame: %v", err)
	}
	if f.Header.TxID != 7 || f.Header.UnitID != 1 {
		t.Fatalf("header mismatch: %+v", f.Header)
	}
	if addr, qty, err := DecodeReadHoldingRequest(f.PDU); err != nil || addr != 0 || qty != 2 {
		t.Fatalf("pdu mismatch: addr=%d qty=%d err=%v", addr, qty, err)
	}
}

// TestReadFrameSticky places two frames back-to-back in one stream; both
// must parse independently with no bytes lost or merged.
func TestReadFrameSticky(t *testing.T) {
	f1 := append(headerBytes(Header{TxID: 1, Length: 6, UnitID: 1}),
		EncodeReadHoldingRequest(0, 1)...)
	f2 := append(headerBytes(Header{TxID: 2, Length: 6, UnitID: 1}),
		EncodeReadHoldingRequest(4, 3)...)
	r := bytes.NewReader(append(f1, f2...))
	g1, err := ReadFrame(r)
	if err != nil {
		t.Fatalf("frame 1: %v", err)
	}
	g2, err := ReadFrame(r)
	if err != nil {
		t.Fatalf("frame 2: %v", err)
	}
	if g1.Header.TxID != 1 || g2.Header.TxID != 2 {
		t.Fatalf("txids: got %d,%d want 1,2", g1.Header.TxID, g2.Header.TxID)
	}
	if r.Len() != 0 {
		t.Fatalf("%d leftover bytes after two frames", r.Len())
	}
}

func TestWriteMultipleRequestQuantityMismatch(t *testing.T) {
	// qty says 2 registers but byte count says 3 bytes: must be rejected.
	pdu := []byte{0x10, 0x00, 0x02, 0x00, 0x02, 0x03, 0x00, 0x0A, 0x01, 0x02, 0x03}
	if _, _, err := DecodeWriteMultipleRequest(pdu); err == nil {
		t.Fatal("expected quantity/byte-count mismatch error")
	}
	// byte count consistent but data truncated.
	pdu2 := []byte{0x10, 0x00, 0x02, 0x00, 0x02, 0x04, 0x00, 0x0A}
	if _, _, err := DecodeWriteMultipleRequest(pdu2); err == nil {
		t.Fatal("expected truncated data error")
	}
}

func TestReadResponseByteCountMismatch(t *testing.T) {
	// Response claims 2 registers (4 bytes) but request asked for 1.
	pdu := []byte{0x03, 0x04, 0x12, 0x34, 0xAB, 0xCD}
	if _, err := DecodeReadHoldingResponse(pdu, 1); err == nil {
		t.Fatal("expected byte-count vs requested-qty mismatch error")
	}
	v, err := DecodeReadHoldingResponse(pdu, 2)
	if err != nil {
		t.Fatalf("valid decode: %v", err)
	}
	if len(v) != 2 || v[0] != 0x1234 || v[1] != 0xABCD {
		t.Fatalf("values: got %v", v)
	}
}

func TestExceptionRoundTrip(t *testing.T) {
	pdu := EncodeException(0x03, 0x02)
	fn, code, ok := DecodeException(pdu)
	if !ok || fn != 0x03 || code != 0x02 {
		t.Fatalf("exception: ok=%v fn=%02X code=%02X", ok, fn, code)
	}
	if _, _, ok := DecodeException([]byte{0x03, 0x04}); ok {
		t.Fatal("normal response misdetected as exception")
	}
}
