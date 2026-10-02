package ber

import (
	"strings"
	"testing"
)

func TestDecodeErrorFormatting(t *testing.T) {
	e := &DecodeError{Kind: KindTruncated, Offset: 7, Depth: 2, Msg: "cut short"}
	s := e.Error()
	for _, want := range []string{"TRUNCATED", "offset 7", "depth 2", "cut short"} {
		if !strings.Contains(s, want) {
			t.Fatalf("error %q missing %q", s, want)
		}
	}
}

func TestLimitsNormalizeFillsZeros(t *testing.T) {
	var l Limits
	n := l.Normalize()
	d := DefaultLimits()
	if n != d {
		t.Fatalf("zero limits did not normalize to defaults:\n got %+v\nwant %+v", n, d)
	}
	// explicit values survive
	custom := Limits{MaxDepth: 3}
	if got := custom.Normalize().MaxDepth; got != 3 {
		t.Fatalf("MaxDepth = %d, want 3", got)
	}
	if got := custom.Normalize().MaxChildren; got != d.MaxChildren {
		t.Fatalf("unset MaxChildren not defaulted: %d", got)
	}
}

func TestClassStrings(t *testing.T) {
	cases := map[Class]string{
		ClassUniversal:   "universal",
		ClassApplication: "application",
		ClassContext:     "context",
		ClassPrivate:     "private",
	}
	for c, want := range cases {
		if c.String() != want {
			t.Fatalf("%d.String() = %q, want %q", c, c.String(), want)
		}
	}
}
