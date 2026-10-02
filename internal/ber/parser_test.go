package ber_test

import (
	"testing"

	"berd/internal/ber"
	"berd/internal/harness"
)

// TestResourceLimits pins every configurable limit to a concrete input and
// the exact offset at which the limit fires.
func TestResourceLimits(t *testing.T) {
	cases := []struct {
		name string
		hex  string
		lim  ber.Limits
		cat  ber.Category
		off  int
	}{
		{
			name: "depth-exceeded",
			// SEQUENCE{SEQUENCE{SEQUENCE{}}}: third level exceeds MaxDepth=2.
			hex: "300430023000",
			lim: ber.Limits{MaxDepth: 2},
			cat: ber.CatResource, off: 4,
		},
		{
			name: "node-count-exceeded",
			// SEQUENCE{1,2} has 3 nodes; limit 2 fires on the second child.
			hex: "3006020101020102",
			lim: ber.Limits{MaxNodes: 2},
			cat: ber.CatResource, off: 5,
		},
		{
			name: "integer-too-big",
			hex:  "0209414243444546474849",
			lim:  ber.Limits{MaxIntegerBytes: 8},
			cat:  ber.CatResource, off: 2,
		},
		{
			name: "bitstring-too-big",
			hex:  "030403ffffff",
			lim:  ber.Limits{MaxBitStringBytes: 2},
			cat:  ber.CatResource, off: 2,
		},
		{
			name: "input-too-big",
			hex:  "020105",
			lim:  ber.Limits{MaxInputBytes: 2},
			cat:  ber.CatResource, off: 2,
		},
		{
			name: "tag-too-long",
			hex:  "9f81800100",
			lim:  ber.Limits{MaxTagBytes: 2},
			cat:  ber.CatResource, off: 0,
		},
		{
			name: "length-of-length-too-big",
			hex:  "02840102030405",
			lim:  ber.Limits{MaxLengthBytes: 3},
			cat:  ber.CatResource, off: 1,
		},
		{
			name: "indefinite-disabled",
			hex:  "30800000",
			lim:  ber.DefaultLimits().DisableIndefinite(),
			cat:  ber.CatIndefinite, off: 1,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := harness.New(t)
			data := harness.MustDecodeHex(t, tc.hex)
			lim := tc.lim.WithDefaults()
			_, err := ber.DecodeAll(data, lim)
			if err == nil {
				err = ber.Validate(mustRoot(t, data, lim), lim)
			}
			h.ExpectError(data, err, tc.cat, tc.off)
		})
	}
}

// TestDepthLimitScales verifies a deeply nested input well beyond any
// reasonable depth is rejected by category resource, not by stack exhaustion.
func TestDepthLimitScales(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits() // MaxDepth 32
	// Build 40 nested empty SEQUENCEs; node at depth k starts at offset 2*(k-1).
	// (40 levels keep every length octet in short form.)
	data := []byte{0x30, 0x00}
	for i := 0; i < 39; i++ {
		wrapped := append([]byte{0x30, byte(len(data))}, data...)
		data = wrapped
	}
	_, err := ber.DecodeAll(data, lim)
	// Depth 33 is the first to exceed the limit; it opens at offset 2*32.
	h.ExpectError(data, err, ber.CatResource, 64)
}

// TestLongFormTagBoundaries checks tag numbers at the short/long boundary.
func TestLongFormTagBoundaries(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()

	// Tag 30 still short form: context primitive tag 30 = 0x9e.
	root, err := ber.DecodeAll(harness.MustDecodeHex(t, "9e00"), lim)
	if err != nil {
		t.Fatalf("tag 30 short form: %v", err)
	}
	h.ExpectEqual("tag 30", root.Tag, uint64(30))

	// Tag 31 requires long form: 0x9f 0x1f.
	root, err = ber.DecodeAll(harness.MustDecodeHex(t, "9f1f00"), lim)
	if err != nil {
		t.Fatalf("tag 31 long form: %v", err)
	}
	h.ExpectEqual("tag 31", root.Tag, uint64(31))

	// Tag 16384 = base-128 0x81 0x80 0x00.
	root, err = ber.DecodeAll(harness.MustDecodeHex(t, "9f81800000"), lim)
	if err != nil {
		t.Fatalf("tag 16384: %v", err)
	}
	h.ExpectEqual("tag 16384", root.Tag, uint64(16384))
}
