package wire_test

import (
	"strings"
	"testing"

	"coaplab/internal/wire"
)

func TestCode_StringClassDetail(t *testing.T) {
	c := wire.Content
	if c.Class() != 2 || c.Detail() != 5 || !strings.Contains(c.String(), "2.05") {
		t.Fatalf("Content rendering: class=%d detail=%d %q", c.Class(), c.Detail(), c.String())
	}
	if wire.RequestEntityIncomplete.String() != "4.08 Request Entity Incomplete" {
		t.Fatalf("4.08 rendering: %q", wire.RequestEntityIncomplete.String())
	}
	if wire.Continue.String() != "2.31 Continue" {
		t.Fatalf("2.31 rendering: %q", wire.Continue.String())
	}
	if wire.GET.String() != "0.01 GET" || wire.Empty.String() != "0.00 Empty" {
		t.Fatalf("request/empty rendering: %q %q", wire.GET, wire.Empty)
	}
	if wire.Type(0).String() != "CON" || wire.NON.String() != "NON" ||
		wire.ACK.String() != "ACK" || wire.RST.String() != "RST" {
		t.Fatal("type rendering wrong")
	}
	// Unknown code falls back to class.detail digits.
	unk := wire.Code(0x5f) // 2.31? 95 is Continue; pick unused 3.22
	unk = wire.Code(0x76)  // 3.22
	if !strings.Contains(unk.String(), "3.22") {
		t.Fatalf("unknown code render: %q", unk.String())
	}
}

func TestUintOption_RoundTrip(t *testing.T) {
	cases := []uint64{0, 1, 13, 269, 65535, 1 << 20}
	for _, n := range cases {
		b := wire.EncodeUint(n)
		if n == 0 && len(b) != 0 {
			t.Fatalf("zero must be 0 bytes, got %d", len(b))
		}
		if wire.DecodeUint(b) != n {
			t.Fatalf("uint round trip %d", n)
		}
	}
}

func TestParseError_Error(t *testing.T) {
	e := &wire.ParseError{Reason: "x"}
	if e.Error() != "wire: x" {
		t.Fatalf("parse error render: %q", e.Error())
	}
}
