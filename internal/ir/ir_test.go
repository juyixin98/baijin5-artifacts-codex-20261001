package ir

import (
	"testing"

	"mylnk/internal/objfmt"
)

// objBuilder hand-authors relocatable objects; reference answers in these
// tests are literal expectations, not derived from the linker under test.
type objBuilder struct {
	o   *objfmt.Object
	sec map[string]*objfmt.Section
	sym map[string]*objfmt.Symbol
}

func newObj(name string) *objBuilder {
	return &objBuilder{
		o:   &objfmt.Object{Name: name},
		sec: map[string]*objfmt.Section{},
		sym: map[string]*objfmt.Symbol{},
	}
}

func (b *objBuilder) section(name string, keep bool) *objBuilder {
	s := &objfmt.Section{Name: name, Keep: keep, Data: []byte{0, 0, 0, 0}}
	b.sec[name] = s
	b.o.Sections = append(b.o.Sections, s)
	return b
}

func (b *objBuilder) def(name, sec string, bind int, export bool) *objBuilder {
	s := b.sec[sec]
	sy := &objfmt.Symbol{Name: name, Bind: bind, Off: 0, Def: true, Export: export, SecIdx: uint16(b.indexOf(s))}
	b.sym[name] = sy
	b.o.Symbols = append(b.o.Symbols, sy)
	s.Syms = append(s.Syms, sy)
	return b
}

func (b *objBuilder) undef(name string, bind int) *objBuilder {
	sy := &objfmt.Symbol{Name: name, Bind: bind, SecIdx: objfmt.UndefSec}
	b.sym[name] = sy
	b.o.Symbols = append(b.o.Symbols, sy)
	return b
}

func (b *objBuilder) reloc(sec, symName string, kind uint8) *objBuilder {
	s := b.sec[sec]
	idx := b.indexOfSym(b.sym[symName])
	s.Relocs = append(s.Relocs, objfmt.Reloc{Off: 0, SymIdx: uint32(idx), Kind: kind})
	return b
}

func (b *objBuilder) indexOf(s *objfmt.Section) int {
	for i, c := range b.o.Sections {
		if c == s {
			return i
		}
	}
	return -1
}

func (b *objBuilder) indexOfSym(sy *objfmt.Symbol) int {
	for i, c := range b.o.Symbols {
		if c == sy {
			return i
		}
	}
	return -1
}

func TestStrongOverridesWeak(t *testing.T) {
	a := newObj("a.o").section(".text.f", false).def("f", ".text.f", objfmt.BindWeak, false)
	b := newObj("b.o").section(".text.f", false).def("f", ".text.f", objfmt.BindStrong, true)
	p, err := Build([]*objfmt.Object{a.o, b.o}, "f")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if p.Globals["f"].WinDef.Obj.Name != "b.o" {
		t.Fatalf("strong definition must win over weak, got %s", p.Globals["f"].WinDef.Obj.Name)
	}
	if p.Globals["f"].Bind != objfmt.BindStrong {
		t.Fatalf("winner bind must be strong")
	}
}

func TestWeakFirstWinsWhenAllWeak(t *testing.T) {
	a := newObj("a.o").section(".t", false).def("g", ".t", objfmt.BindWeak, false)
	b := newObj("b.o").section(".t", false).def("g", ".t", objfmt.BindWeak, false)
	p, err := Build([]*objfmt.Object{a.o, b.o}, "g")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if p.Globals["g"].WinDef.Obj.Name != "a.o" {
		t.Fatalf("first weak definition wins, got %s", p.Globals["g"].WinDef.Obj.Name)
	}
}

func TestMultipleStrongRejected(t *testing.T) {
	a := newObj("a.o").section(".t", false).def("h", ".t", objfmt.BindStrong, false)
	b := newObj("b.o").section(".t", false).def("h", ".t", objfmt.BindStrong, false)
	_, err := Build([]*objfmt.Object{a.o, b.o}, "h")
	if err == nil {
		t.Fatal("expected multiple-strong-definition error")
	}
	le, ok := err.(*LinkError)
	if !ok || le.Kind != KindMultipleStrong {
		t.Fatalf("want kind %s, got %v", KindMultipleStrong, err)
	}
}

func TestReachabilityTransitiveAndGCDrop(t *testing.T) {
	// entry -> calls middle -> calls leaf ; dead section references missing.
	main := newObj("m.o").
		section(".text.main", false).def("main", ".text.main", objfmt.BindStrong, true).
		undef("middle", objfmt.BindStrong).reloc(".text.main", "middle", objfmt.KindRel32)
	mid := newObj("mid.o").
		section(".text.mid", false).def("middle", ".text.mid", objfmt.BindStrong, false).
		undef("leaf", objfmt.BindStrong).reloc(".text.mid", "leaf", objfmt.KindRel32)
	leaf := newObj("leaf.o").
		section(".text.leaf", false).def("leaf", ".text.leaf", objfmt.BindStrong, false)
	// dead code referencing an otherwise-missing symbol must not cause errors.
	dead := newObj("dead.o").
		section(".text.dead", false).def("deadfn", ".text.dead", objfmt.BindStrong, false).
		undef("ghost", objfmt.BindStrong).reloc(".text.dead", "ghost", objfmt.KindAbs32)
	p, err := Build([]*objfmt.Object{main.o, mid.o, leaf.o, dead.o}, "main")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	wantKept := []string{"m.o:.text.main", "mid.o:.text.mid", "leaf.o:.text.leaf"}
	for _, w := range wantKept {
		found := false
		for id := range rr.Kept {
			gs := p.Sections[id]
			if gs.Obj.Name+":"+gs.Name == w {
				found = true
			}
		}
		if !found {
			t.Fatalf("expected %s to be reachable", w)
		}
	}
	if len(rr.Dropped) != 1 || rr.Dropped[0].Name != ".text.dead" {
		t.Fatalf("only dead section should be dropped, got %+v", rr.Dropped)
	}
	if errs := p.UndefinedDiagnostics(rr); len(errs) != 0 {
		t.Fatalf("no undefined errors expected (ghost is in dropped section), got %v", errs)
	}
}

func TestExportRootKeepsSection(t *testing.T) {
	a := newObj("a.o").
		section(".data.tbl", true).
		section(".text.api", false).def("api", ".text.api", objfmt.BindStrong, true)
	p, err := Build([]*objfmt.Object{a.o}, "")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	if len(rr.Kept) != 2 {
		t.Fatalf("keep+export sections are roots, kept=%d", len(rr.Kept))
	}
}

func TestUndefinedStrongDiagnosed(t *testing.T) {
	a := newObj("a.o").
		section(".text.main", false).def("main", ".text.main", objfmt.BindStrong, true).
		undef("missing", objfmt.BindStrong).reloc(".text.main", "missing", objfmt.KindRel32)
	p, err := Build([]*objfmt.Object{a.o}, "main")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	errs := p.UndefinedDiagnostics(rr)
	if len(errs) != 1 || errs[0].Kind != KindUndefinedSymbol {
		t.Fatalf("want one undefined-symbol error, got %v", errs)
	}
}

func TestWeakUndefinedAllowed(t *testing.T) {
	a := newObj("a.o").
		section(".text.main", false).def("main", ".text.main", objfmt.BindStrong, true).
		undef("maybe", objfmt.BindWeak).reloc(".text.main", "maybe", objfmt.KindRel32)
	p, err := Build([]*objfmt.Object{a.o}, "main")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	if errs := p.UndefinedDiagnostics(rr); len(errs) != 0 {
		t.Fatalf("weak undefined must be allowed, got %v", errs)
	}
}
