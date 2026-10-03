package linker

import (
	"testing"

	"mylnk/internal/ir"
	"mylnk/internal/objfmt"
)

// TestApplyRelocationsOverflow builds a minimal IR where a rel32 site in
// a kept section targets a symbol far enough to overflow int32. Objects
// carry small data; only the assigned addresses are made distant.
func TestApplyRelocationsOverflow(t *testing.T) {
	caller := &objfmt.Section{Name: ".text.main", Data: []byte{0x05, 0, 0, 0, 0}}
	callee := &objfmt.Section{Name: ".text.callee", Data: []byte{0x06}}
	mainSym := &objfmt.Symbol{Name: "main", Bind: objfmt.BindStrong, Def: true, Export: true, SecIdx: 0}
	farSym := &objfmt.Symbol{Name: "farcall", Bind: objfmt.BindStrong, Def: true, SecIdx: 1}
	caller.Syms = []*objfmt.Symbol{mainSym}
	callee.Syms = []*objfmt.Symbol{farSym}
	obj := &objfmt.Object{
		Name:     "x.o",
		Sections: []*objfmt.Section{caller, callee},
		Symbols:  []*objfmt.Symbol{mainSym, farSym},
	}
	caller.Relocs = []objfmt.Reloc{{Off: 1, SymIdx: 1, Kind: objfmt.KindRel32}}

	p, err := ir.Build([]*objfmt.Object{obj}, "main")
	if err != nil {
		t.Fatal(err)
	}
	rr := p.AnalyzeReach()

	// Force an overflowing layout: caller at base, callee base + 0x90000000.
	origLayout := Layout
	_ = origLayout
	img, err := linkWithSyntheticLayout(p, rr, map[ir.SectionID]uint32{
		{Obj: 0, Sec: 0}: BaseAddr,
		{Obj: 0, Sec: 1}: BaseAddr + 0x90000000,
	})
	if err == nil {
		_ = img
		t.Fatal("expected relocation-overflow")
	}
	le, ok := err.(*ir.LinkError)
	if !ok || le.Kind != ir.KindRelocOverflow {
		t.Fatalf("want relocation-overflow, got %v", err)
	}
}
