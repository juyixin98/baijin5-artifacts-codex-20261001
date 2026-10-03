package runtime

import (
	"testing"

	"mylnk/internal/ir"
	"mylnk/internal/linker"
)

// pushi 123; halt => 123
func TestRunSimpleImage(t *testing.T) {
	mem := []byte{
		0x01, 123, 0, 0, 0,
		0x0F,
	}
	img := &linker.Image{Memory: mem, Base: linker.BaseAddr, EntryPC: linker.BaseAddr}
	res, err := Run(img, 0, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Return != 123 || !res.Halted {
		t.Fatalf("result=%+v", res)
	}
}

// Entry at null (weak undefined) is an explicit runtime-category error.
func TestNullEntryClassified(t *testing.T) {
	img := &linker.Image{Memory: []byte{0x0F}, Base: linker.BaseAddr, EntryPC: 0}
	_, err := Run(img, 0, false)
	if err == nil {
		t.Fatal("null entry must fail")
	}
	le := err.(*ir.LinkError)
	if le.Kind != ir.KindRuntime {
		t.Fatalf("kind=%s", le.Kind)
	}
}

// Image placed at a non-default base indexes memory relative to Base.
func TestNonDefaultBase(t *testing.T) {
	mem := []byte{
		0x01, 0x2A, 0, 0, 0, // pushi 42
		0x0F,
	}
	base := uint32(0x200000)
	img := &linker.Image{Memory: mem, Base: base, EntryPC: base}
	res, err := Run(img, 0, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Return != 42 {
		t.Fatalf("return=%d want 42", res.Return)
	}
}
