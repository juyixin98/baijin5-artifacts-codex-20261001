package frame

import (
	"bytes"
	"io"
	"testing"
)

func TestHeaderRoundTrip(t *testing.T) {
	cases := []Header{
		{Length: 0, Type: Settings, Flags: FlagAck, StreamID: 0},
		{Length: 16384, Type: Data, Flags: 0, StreamID: 1},
		{Length: MaxAllowedFrameSize, Type: Headers, Flags: FlagEndHeaders | FlagEndStream, StreamID: 0x7fffffff},
		{Length: 8, Type: Ping, Flags: 0, StreamID: 0},
	}
	for _, want := range cases {
		got := ParseHeader(want.Marshal())
		if got != want {
			t.Fatalf("roundtrip: want %+v, got %+v", want, got)
		}
	}
}

func TestReservedStreamBitMasked(t *testing.T) {
	h := Header{Length: 1, Type: Data, StreamID: 3}
	b := h.Marshal()
	b[5] |= 0x80 // set the reserved high bit
	got := ParseHeader(b)
	if got.StreamID != 3 {
		t.Fatalf("reserved bit must be masked: got %d", got.StreamID)
	}
}

func TestReadHeader(t *testing.T) {
	want := Header{Length: 4, Type: WindowUpdate, StreamID: 1}
	m := want.Marshal()
	got, err := ReadHeader(bytes.NewReader(m[:]))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if got != want {
		t.Fatalf("want %+v, got %+v", want, got)
	}
	if _, err := ReadHeader(bytes.NewReader(nil)); err != io.EOF {
		t.Fatalf("empty reader: want EOF, got %v", err)
	}
	if _, err := ReadHeader(bytes.NewReader(m[:5])); err == nil {
		t.Fatal("short read must fail")
	}
}

func TestErrCodeNames(t *testing.T) {
	if got := ProtocolError.String(); got != "PROTOCOL_ERROR" {
		t.Fatalf("got %s", got)
	}
	if got := EnhanceYourCalm.String(); got != "ENHANCE_YOUR_CALM" {
		t.Fatalf("got %s", got)
	}
	if got := ErrCode(0xff).String(); got != "UNKNOWN(0xff)" {
		t.Fatalf("got %s", got)
	}
}

func TestTypeNames(t *testing.T) {
	if got := Continuation.String(); got != "CONTINUATION" {
		t.Fatalf("got %s", got)
	}
	if got := Type(0xf0).String(); got != "UNKNOWN(0xf0)" {
		t.Fatalf("got %s", got)
	}
}
