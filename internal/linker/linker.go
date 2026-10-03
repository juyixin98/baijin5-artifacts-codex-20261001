// Package linker lays out reachable sections, applies relocations and
// produces an executable image for the runtime interpreter.
package linker

import (
	"encoding/binary"
	"fmt"

	"mylnk/internal/ir"
	"mylnk/internal/objfmt"
)

// BaseAddr is where the image starts in virtual address space.
const BaseAddr = 0x10000

// PlacedSection records assigned addresses.
type PlacedSection struct {
	Sec  *ir.Section
	Addr uint32
	Size uint32
}

// SymbolAddress is one exported/global address table entry.
type SymbolAddress struct {
	Name   string
	Addr   uint32
	Weak   bool
	IsNull bool // weak-undefined: resolves to 0
}

// Image is the linked program image.
type Image struct {
	Memory   []byte
	Base     uint32 // virtual address of Memory[0]
	Sections []PlacedSection
	Symbols  map[string]uint32
	EntryPC  uint32
	Entry    string
}

// Layout places kept sections in deterministic (object, section) order.
func Layout(p *ir.Program, rr *ir.ReachResult) ([]PlacedSection, map[*ir.Section]*PlacedSection) {
	var placed []PlacedSection
	bySec := map[*ir.Section]*PlacedSection{}
	addr := uint32(BaseAddr)
	for _, gs := range p.Order {
		if !rr.Kept[gs.ID] {
			continue
		}
		ps := &PlacedSection{Sec: gs, Addr: addr, Size: uint32(len(gs.Native.Data))}
		placed = append(placed, *ps)
		bySec[gs] = ps
		addr += ps.Size
	}
	return placed, bySec
}

// Link runs layout + relocation and assembles the final image.
// It enforces the "no dangling relocation against a dropped section"
// invariant explicitly.
func Link(p *ir.Program, rr *ir.ReachResult) (*Image, error) {
	placed, bySec := Layout(p, rr)
	return linkPlaced(p, rr, placed, bySec)
}

// linkPlaced applies the link pipeline over an explicit placement.
func linkPlaced(p *ir.Program, rr *ir.ReachResult, placed []PlacedSection, bySec map[*ir.Section]*PlacedSection) (*Image, error) {
	var minAddr, maxAddr uint32
	minAddr = ^uint32(0)
	if len(placed) == 0 {
		minAddr = BaseAddr
	}
	for i := range placed {
		if placed[i].Addr < minAddr {
			minAddr = placed[i].Addr
		}
		if end := placed[i].Addr + placed[i].Size; end > maxAddr {
			maxAddr = end
		}
	}
	mem := make([]byte, uint64(maxAddr)-uint64(minAddr))
	for _, ps := range placed {
		copy(mem[ps.Addr-minAddr:ps.Addr-minAddr+ps.Size], ps.Sec.Native.Data)
	}

	symAddr := map[string]uint32{}
	for name, g := range p.Globals {
		if g.WinDef == nil {
			if g.WeakRef {
				symAddr[name] = 0
			}
			continue
		}
		if ps := bySec[g.WinDef]; ps != nil {
			symAddr[name] = ps.Addr + g.Off
		}
	}

	resolveTarget := func(rl ir.ResolvedReloc) (uint32, error, string) {
		switch {
		case rl.Local != nil:
			lps := bySec[rl.Local]
			if lps == nil {
				return 0, &ir.LinkError{Kind: ir.KindInternalInvariant,
					Msg: "local relocation targets a reclaimed section"}, ""
			}
			return lps.Addr + rl.LocalOff, nil, ""
		case rl.Target != nil && rl.Target.WinDef != nil:
			tps := bySec[rl.Target.WinDef]
			if tps == nil {
				return 0, &ir.LinkError{Kind: ir.KindInternalInvariant,
					Msg: "relocation targets a reclaimed section"}, ""
			}
			return tps.Addr + rl.Target.Off, nil, ""
		case rl.Target != nil && rl.Target.WinDef == nil && rl.Target.WeakRef:
			return 0, nil, ""
		default:
			return 0, &ir.LinkError{Kind: ir.KindUndefinedSymbol,
				Msg: "unresolved strong symbol " + safeName(rl.Target)}, ""
		}
	}

	for _, ps := range placed {
		gs := ps.Sec
		for _, rl := range gs.Relocs {
			target, err0, _ := resolveTarget(rl)
			if err0 != nil {
				return nil, err0
			}
			site := ps.Addr + rl.Off
			switch rl.Kind {
			case objfmt.KindAbs32:
				val := int64(target) + int64(rl.Addend)
				if val > 0xFFFFFFFF || val < -0x80000000 {
					return nil, &ir.LinkError{Kind: ir.KindRelocOverflow,
						Msg: fmt.Sprintf("abs32 overflow at %s+%#x", gs.Name, rl.Off)}
				}
				binary.LittleEndian.PutUint32(mem[site-minAddr:site-minAddr+4], uint32(val))
			case objfmt.KindRel32:
				pc := int64(site) + 4
				val := int64(target) + int64(rl.Addend) - pc
				if val > 0x7FFFFFFF || val < -0x80000000 {
					return nil, &ir.LinkError{Kind: ir.KindRelocOverflow,
						Msg: fmt.Sprintf("rel32 overflow at %s+%#x", gs.Name, rl.Off)}
				}
				binary.LittleEndian.PutUint32(mem[site-minAddr:site-minAddr+4], uint32(int32(val)))
			default:
				return nil, &ir.LinkError{Kind: ir.KindBadObject,
					Msg: fmt.Sprintf("unknown reloc kind %d", rl.Kind)}
			}
		}
	}

	if err := auditNoDangling(placed, minAddr, uint64(maxAddr)-uint64(minAddr)); err != nil {
		return nil, err
	}

	entryPC := uint32(0)
	if g := p.Globals[p.Entry]; g != nil && g.WinDef != nil {
		if ps := bySec[g.WinDef]; ps != nil {
			entryPC = ps.Addr + g.Off
		}
	}
	return &Image{
		Memory:   mem,
		Base:     minAddr,
		Sections: placed,
		Symbols:  symAddr,
		EntryPC:  entryPC,
		Entry:    p.Entry,
	}, nil
}

func safeName(g *ir.Symbol) string {
	if g == nil {
		return "<nil>"
	}
	return g.Name
}

func auditNoDangling(placed []PlacedSection, base uint32, size uint64) error {
	for _, ps := range placed {
		if ps.Addr < base || uint64(ps.Addr)+uint64(ps.Size) > uint64(base)+size {
			return &ir.LinkError{Kind: ir.KindInternalInvariant,
				Msg: fmt.Sprintf("section %s image out of bounds", ps.Sec.Name)}
		}
	}
	return nil
}
