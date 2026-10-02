package mbap

import (
	"bytes"
	"encoding/hex"
	"errors"
	"io"
	"strings"
	"testing"

	"modbusfixture/vectors"
)

func decodeHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("bad test hex %q: %v", s, err)
	}
	return b
}

func TestEncodeGolden(t *testing.T) {
	cases := []struct {
		name string
		h    Header
		pdu  string
		want string
	}{
		{"fc03_request", Header{TxnID: 0x1234, UnitID: 0x05},
			"0300000002", "123400000006050300000002"},
		{"fc03_response", Header{TxnID: 0x1234, UnitID: 0x05},
			"030411223344", "12340000000705030411223344"},
		{"fc16_request", Header{TxnID: 0x0001, UnitID: 0x01},
			"10000a00020400010002", "00010000000b0110000a00020400010002"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Encode(nil, tc.h, decodeHex(t, tc.pdu))
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if hex.EncodeToString(got) != tc.want {
				t.Fatalf("Encode = %x, want %s", got, tc.want)
			}
		})
	}
}

func TestDecodeGoldenRoundTrip(t *testing.T) {
	for _, g := range vectors.Golden {
		if g.ReqHex == "" {
			continue
		}
		t.Run(g.Name, func(t *testing.T) {
			raw := vectors.MustHex(g.ReqHex)
			h, pdu, err := Decode(raw)
			if err != nil {
				t.Fatalf("Decode golden: %v", err)
			}
			if h.TxnID != uint16(raw[0])<<8|uint16(raw[1]) {
				t.Errorf("txn id mismatch: got %04x", h.TxnID)
			}
			if h.ProtocolID != 0 {
				t.Errorf("protocol id = %04x", h.ProtocolID)
			}
			if int(h.Length) != 1+len(pdu) {
				t.Errorf("length %d != 1+pdu %d", h.Length, len(pdu))
			}
			out, err := Encode(nil, h, pdu)
			if err != nil {
				t.Fatalf("re-encode: %v", err)
			}
			if !bytes.Equal(out, raw) {
				t.Fatalf("round trip changed bytes:\n got %x\nwant %x", out, raw)
			}
		})
	}
}

func TestDecodeFailuresByKind(t *testing.T) {
	for _, c := range vectors.FramingMalformed {
		t.Run(c.Name, func(t *testing.T) {
			// trailing_bytes is a Decode-only failure class: the stream
			// reader treats a second frame boundary as the next frame.
			_, _, err := Decode(vectors.MustHex(c.Hex))
			if c.Category == vectors.CatTrailingBytes {
				if err == nil {
					t.Fatal("want KindTrailingBytes, got nil")
				}
				assertKind(t, err, Kind(c.Category))
				return
			}
			if err == nil {
				t.Fatalf("want %s, got nil", c.Category)
			}
			assertKind(t, err, Kind(c.Category))
		})
	}
}

func assertKind(t *testing.T, err error, want Kind) {
	t.Helper()
	var fe *FrameError
	if !errors.As(err, &fe) {
		t.Fatalf("error %T is not *FrameError: %v", err, err)
	}
	if fe.Kind != want {
		t.Fatalf("error kind = %q, want %q", fe.Kind, want)
	}
}

// TestReaderHalfAndStickyPackets feeds one frame split across many writes
// (half packet) followed immediately by two frames in one write (sticky
// packets). The reader must emit three frames with the right identities.
func TestReaderHalfAndStickyPackets(t *testing.T) {
	f1 := vectors.MustHex(vectors.Golden[0].ReqHex)
	f2 := vectors.MustHex(vectors.Golden[1].ReqHex)
	f3 := vectors.MustHex(vectors.Golden[3].ReqHex)

	var buf bytes.Buffer
	// f1 byte by byte
	for _, b := range f1 {
		buf.WriteByte(b)
	}
	// f2 and f3 glued in one chunk
	buf.Write(f2)
	buf.Write(f3)

	r := NewReader(&buf)
	wantTxns := []uint16{0x1234, 0x0001, 0x0010}
	wantUnits := []byte{0x05, 0x01, 0xFF}
	for i := range wantTxns {
		h, pdu, err := r.ReadFrame()
		if err != nil {
			t.Fatalf("frame %d: %v", i, err)
		}
		if h.TxnID != wantTxns[i] {
			t.Errorf("frame %d txn = %04x, want %04x", i, h.TxnID, wantTxns[i])
		}
		if h.UnitID != wantUnits[i] {
			t.Errorf("frame %d unit = %02x, want %02x", i, h.UnitID, wantUnits[i])
		}
		if len(pdu) != int(h.Length)-1 {
			t.Errorf("frame %d pdu len %d != length %d-1", i, len(pdu), h.Length)
		}
	}
	if _, _, err := r.ReadFrame(); !errors.Is(err, ErrStreamClosed) {
		t.Fatalf("closed stream: got %v, want ErrStreamClosed", err)
	}
}

// TestReaderSplitHeader puts the boundary in the middle of the MBAP header.
func TestReaderSplitHeader(t *testing.T) {
	f := vectors.MustHex(vectors.Golden[0].ReqHex)
	pr, pw := io.Pipe()
	go func() {
		_, _ = pw.Write(f[:3])
		_, _ = pw.Write(f[3:])
		_ = pw.Close()
	}()
	r := NewReader(pr)
	h, pdu, err := r.ReadFrame()
	if err != nil {
		t.Fatalf("ReadFrame: %v", err)
	}
	if h.TxnID != 0x1234 || len(pdu) != 5 {
		t.Fatalf("unexpected frame: %+v pdu=%x", h, pdu)
	}
}

func TestReaderTruncatedBody(t *testing.T) {
	r := NewReader(bytes.NewReader(vectors.MustHex("12340000000605030000")))
	_, _, err := r.ReadFrame()
	if !errors.Is(err, ErrStreamClosed) {
		t.Fatalf("got %v, want ErrStreamClosed", err)
	}
}

func TestReaderIllegalLength(t *testing.T) {
	r := NewReader(bytes.NewReader(vectors.MustHex("1234000000000503")))
	_, _, err := r.ReadFrame()
	assertKind(t, err, KindLengthTooSmall)
}

func TestEncodeRejectsOversizedPDU(t *testing.T) {
	big := make([]byte, MaxPDULength+1)
	if _, err := Encode(nil, Header{}, big); err == nil ||
		!strings.Contains(err.Error(), string(KindLengthTooLarge)) {
		t.Fatalf("want length_too_large, got %v", err)
	}
}
