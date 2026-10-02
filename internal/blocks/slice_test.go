package blocks_test

import (
	"testing"

	"coaplab/internal/blocks"
)

// An empty representation answers Block2 NUM 0 with one final empty block,
// not a 4.00 gap.
func TestSlice_EmptyBody(t *testing.T) {
	p, m, f := blocks.Slice(nil, 0, 2)
	if f != nil {
		t.Fatalf("empty body NUM0: %v", f)
	}
	if m || len(p) != 0 {
		t.Fatalf("empty body block: m=%v len=%d", m, len(p))
	}
	if _, _, f := blocks.Slice(nil, 1, 2); f == nil {
		t.Fatal("NUM>0 of empty body must fail")
	}
}

// Slice bounds: block beyond the body is a classified gap.
func TestSlice_BeyondBody(t *testing.T) {
	body := make([]byte, 10) // one 16-byte block, NUM 0
	if _, _, f := blocks.Slice(body, 1, 0); f == nil {
		t.Fatal("NUM=1 beyond 10-byte body must fail")
	}
	p, m, f := blocks.Slice(body, 0, 0)
	if f != nil || m || len(p) != 10 {
		t.Fatalf("NUM0 tail: m=%v len=%d f=%v", m, len(p), f)
	}
}
