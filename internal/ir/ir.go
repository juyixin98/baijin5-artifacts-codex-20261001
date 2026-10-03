// Package ir transforms parsed relocatable objects into a linked model:
// it performs global (strong/weak) symbol resolution with fixed precedence
// rules and computes section reachability for section reclamation.
package ir

import (
	"fmt"
	"sort"

	"mylnk/internal/objfmt"
)

// SectionID identifies a section across all input objects.
type SectionID struct {
	Obj int
	Sec int
}

// Section is a global view of one input section.
type Section struct {
	ID     SectionID
	Obj    *objfmt.Object
	Native *objfmt.Section
	Name   string
	Keep   bool

	// Defined global symbols (resolved winners and locals) in this section.
	Defs []*Symbol

	// Resolved relocations against global winner symbols or local defs.
	Relocs []ResolvedReloc

	Reachable bool
}

// Symbol is the global, resolution-after view of a name.
type Symbol struct {
	Name    string
	WinDef  *Section // winning definition (nil if left undefined)
	Off     uint32
	Bind    int
	Export  bool
	WeakRef bool // any reference side marked it weak
	Refs    []SectionID
}

// ResolvedReloc binds a site to its target symbol or a local section-relative def.
type ResolvedReloc struct {
	Off      uint32
	Kind     uint8
	Addend   int32
	Target   *Symbol
	Local    *Section // intra-object local definition, resolved straight to section
	LocalOff uint32
}

// LinkError categorises a semantic failure.
type LinkError struct {
	Kind string
	Msg  string
}

func (e *LinkError) Error() string { return e.Kind + ": " + e.Msg }

// Error kinds used by the linker pipeline.
const (
	KindMultipleStrong    = "multiple-strong-definition"
	KindUndefinedSymbol   = "undefined-symbol"
	KindRelocOverflow     = "relocation-overflow"
	KindInternalInvariant = "internal-invariant"
	KindBadObject         = "bad-object"
	KindRuntime           = "runtime"
)

// Program is the resolved intermediate representation.
type Program struct {
	Objects  []*objfmt.Object
	Sections map[SectionID]*Section
	Order    []*Section
	Globals  map[string]*Symbol
	Entry    string
}

// Build constructs the IR and performs global symbol resolution.
func Build(objs []*objfmt.Object, entry string) (*Program, error) {
	p := &Program{
		Objects:  objs,
		Sections: map[SectionID]*Section{},
		Globals:  map[string]*Symbol{},
		Entry:    entry,
	}
	for oi, o := range objs {
		for si, ns := range o.Sections {
			id := SectionID{oi, si}
			gs := &Section{ID: id, Obj: o, Native: ns, Name: ns.Name, Keep: ns.Keep}
			p.Sections[id] = gs
			p.Order = append(p.Order, gs)
		}
	}

	// global(name) helper
	glob := func(name string) *Symbol {
		sy := p.Globals[name]
		if sy == nil {
			sy = &Symbol{Name: name}
			p.Globals[name] = sy
		}
		return sy
	}

	// Register definitions following deterministic input order.
	for oi, o := range objs {
		secByPtr := map[*objfmt.Section]int{}
		for i, ns := range o.Sections {
			secByPtr[ns] = i
		}
		for _, raw := range o.Symbols {
			if !raw.Def {
				continue
			}
			si, ok := secByPtr[sectionOf(o, raw)]
			if !ok {
				return nil, &LinkError{Kind: KindBadObject, Msg: fmt.Sprintf("symbol %q not attached to a section", raw.Name)}
			}
			gs := p.Sections[SectionID{oi, si}]
			switch raw.Bind {
			case objfmt.BindLocal:
				gs.Defs = append(gs.Defs, &Symbol{
					Name: raw.Name, WinDef: gs, Off: raw.Off, Bind: raw.Bind, Export: false,
				})
			case objfmt.BindStrong, objfmt.BindWeak:
				g := glob(raw.Name)
				g.Export = g.Export || raw.Export
				if g.WinDef == nil {
					g.WinDef = gs
					g.Off = raw.Off
					g.Bind = raw.Bind
				} else {
					curStrong := g.Bind == objfmt.BindStrong
					newStrong := raw.Bind == objfmt.BindStrong
					switch {
					case curStrong && newStrong:
						return nil, &LinkError{
							Kind: KindMultipleStrong,
							Msg: fmt.Sprintf("strong symbol %q defined in %s and %s",
								raw.Name, where(g.WinDef), where(gs)),
						}
					case !curStrong && newStrong:
						// strong overrides earlier weak
						g.WinDef = gs
						g.Off = raw.Off
						g.Bind = objfmt.BindStrong
					case curStrong && !newStrong:
						// weak loses silently
					default:
						// weak vs weak: first definition wins
					}
				}
			}
		}
	}

	// Resolve every relocation target and record reference metadata.
	for _, gs := range p.Order {
		o := gs.Obj
		for _, rl := range gs.Native.Relocs {
			raw := o.Symbols[rl.SymIdx]
			if raw.Bind == objfmt.BindLocal {
				var d *Symbol
				for _, cand := range gs.Defs {
					if cand.Name == raw.Name {
						d = cand
					}
				}
				if d == nil {
					// Local reference to a local symbol defined in another section
					// of the same object: resolve as section+offset target.
					target := p.findLocalInObject(o, raw.Name)
					if target == nil {
						return nil, &LinkError{Kind: KindBadObject, Msg: fmt.Sprintf("local symbol %q referenced but not defined in %s", raw.Name, o.Name)}
					}
					gs.Relocs = append(gs.Relocs, ResolvedReloc{
						Off: rl.Off, Kind: rl.Kind, Addend: rl.Addend,
						Local: target.gs, LocalOff: target.off,
					})
					continue
				}
				gs.Relocs = append(gs.Relocs, ResolvedReloc{
					Off: rl.Off, Kind: rl.Kind, Addend: rl.Addend,
					Local: gs, LocalOff: d.Off,
				})
				continue
			}
			g := glob(raw.Name)
			g.WeakRef = g.WeakRef || raw.Bind == objfmt.BindWeak
			g.Refs = append(g.Refs, gs.ID)
			gs.Relocs = append(gs.Relocs, ResolvedReloc{
				Off: rl.Off, Kind: rl.Kind, Addend: rl.Addend, Target: g,
			})
		}
	}
	// Stable order for deterministic processing.
	sort.SliceStable(p.Order, func(i, j int) bool {
		if p.Order[i].ID.Obj != p.Order[j].ID.Obj {
			return p.Order[i].ID.Obj < p.Order[j].ID.Obj
		}
		return p.Order[i].ID.Sec < p.Order[j].ID.Sec
	})
	return p, nil
}

type localHit struct {
	gs  *Section
	off uint32
}

func (p *Program) findLocalInObject(o *objfmt.Object, name string) *localHit {
	for oi, cand := range p.Objects {
		if cand != o {
			continue
		}
		for si, ns := range cand.Sections {
			for _, sy := range ns.Syms {
				if sy.Name == name && sy.Def && sy.Bind == objfmt.BindLocal {
					return &localHit{gs: p.Sections[SectionID{oi, si}], off: sy.Off}
				}
			}
		}
	}
	return nil
}

// sectionOf finds the section containing a defined symbol using Syms links.
func sectionOf(o *objfmt.Object, sy *objfmt.Symbol) *objfmt.Section {
	for _, ns := range o.Sections {
		for _, s := range ns.Syms {
			if s == sy {
				return ns
			}
		}
	}
	return nil
}

func where(gs *Section) string {
	return fmt.Sprintf("%s:%s", gs.Obj.Name, gs.Name)
}
