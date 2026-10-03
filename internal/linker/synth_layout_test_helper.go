package linker

import (
	"mylnk/internal/ir"
)

// linkWithSyntheticLayout mirrors Link but assigns caller-provided base
// addresses. Test-only helper.
func linkWithSyntheticLayout(p *ir.Program, rr *ir.ReachResult, bases map[ir.SectionID]uint32) (*Image, error) {
	placed := []PlacedSection{}
	bySec := map[*ir.Section]*PlacedSection{}
	for _, gs := range p.Order {
		if !rr.Kept[gs.ID] {
			continue
		}
		base, ok := bases[gs.ID]
		if !ok {
			continue
		}
		ps := &PlacedSection{Sec: gs, Addr: base, Size: uint32(len(gs.Native.Data))}
		placed = append(placed, *ps)
		bySec[gs] = ps
	}
	return linkPlaced(p, rr, placed, bySec)
}
