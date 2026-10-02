package mbap

import (
	"bytes"
	"errors"
	"io"
	"testing"
)

// errReader fails every read with a fixed non-EOF error.
type errReader struct{ err error }

func (e errReader) Read(_ []byte) (int, error) { return 0, e.err }

func TestPartialHeaderDetection(t *testing.T) {
	for _, n := range []int{1, 2, 3, 5, 6} {
		r := NewReader(bytes.NewReader(bytes.Repeat([]byte{0xAB}, n)))
		_, _, err := r.ReadFrame()
		if !errors.Is(err, ErrStreamClosed) {
			t.Fatalf("n=%d: got %v, want ErrStreamClosed", n, err)
		}
		kind, partial := r.PartialFrame()
		if !partial || kind != KindHeaderTruncated {
			t.Fatalf("n=%d: PartialFrame = %q,%v want header_truncated,true",
				n, kind, partial)
		}
	}
}

func TestCleanEOFIsNotPartial(t *testing.T) {
	r := NewReader(bytes.NewReader(nil))
	_, _, err := r.ReadFrame()
	if !errors.Is(err, ErrStreamClosed) {
		t.Fatalf("got %v, want ErrStreamClosed", err)
	}
	if _, partial := r.PartialFrame(); partial {
		t.Fatal("clean immediate EOF must not report a partial frame")
	}
}

func TestPartialBodyDetection(t *testing.T) {
	// Valid 7-byte header announcing length=6 (5 PDU bytes) but only 2.
	raw := []byte{0x12, 0x34, 0x00, 0x00, 0x00, 0x06, 0x05, 0x03, 0x00}
	r := NewReader(bytes.NewReader(raw))
	h, _, err := r.ReadFrame()
	if !errors.Is(err, ErrStreamClosed) {
		t.Fatalf("got %v, want ErrStreamClosed", err)
	}
	kind, partial := r.PartialFrame()
	if !partial || kind != KindBodyTruncated {
		t.Fatalf("PartialFrame = %q,%v want body_truncated,true", kind, partial)
	}
	// The complete header must still be surfaced for audit correlation.
	if h.TxnID != 0x1234 || h.UnitID != 0x05 || h.Length != 6 {
		t.Fatalf("partial header not surfaced: %+v", h)
	}
}

func TestPartialHeaderFieldReconstruction(t *testing.T) {
	cases := []struct {
		b       []byte
		wantTxn uint16
		wantLen uint16
		wantU   byte
	}{
		{[]byte{0x12}, 0, 0, 0},
		{[]byte{0x12, 0x34}, 0x1234, 0, 0},
		{[]byte{0x12, 0x34, 0, 0}, 0x1234, 0, 0},
		{[]byte{0x12, 0x34, 0, 0, 0, 6}, 0x1234, 6, 0},
	}
	for _, c := range cases {
		h := partialHeader(c.b)
		if h.TxnID != c.wantTxn || h.Length != c.wantLen || h.UnitID != c.wantU {
			t.Fatalf("partialHeader(% x) = %+v", c.b, h)
		}
	}
}

func TestReadFramePropagatesNonEOFError(t *testing.T) {
	sentinel := errors.New("synthetic read failure")
	r := NewReader(errReader{err: sentinel})
	_, _, err := r.ReadFrame()
	if !errors.Is(err, sentinel) {
		t.Fatalf("got %v, want synthetic read failure", err)
	}
}

func TestReaderIllegalLengthTooSmall(t *testing.T) {
	// length=1: reader surfaces KindLengthTooSmall directly.
	raw := []byte{0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x05, 0x03}
	r := NewReader(bytes.NewReader(raw))
	_, _, err := r.ReadFrame()
	var fe *FrameError
	if !errors.As(err, &fe) || fe.Kind != KindLengthTooSmall {
		t.Fatalf("got %v, want length_too_small", err)
	}
}

func TestReaderIllegalLengthTooLarge(t *testing.T) {
	raw := []byte{0x00, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x05, 0x03}
	r := NewReader(bytes.NewReader(raw))
	_, _, err := r.ReadFrame()
	var fe *FrameError
	if !errors.As(err, &fe) || fe.Kind != KindLengthTooLarge {
		t.Fatalf("got %v, want length_too_large", err)
	}
}

var _ = io.EOF
