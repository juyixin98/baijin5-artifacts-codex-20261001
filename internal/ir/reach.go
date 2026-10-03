package ir

import "mylnk/internal/objfmt"

// ReachResult records section reclamation decisions and their basis.
type ReachResult struct {
	Kept    map[SectionID]bool
	Dropped []*Section
	Roots   []rootReason
	Edges   map[SectionID][]SectionID
}

type rootReason struct {
	Sec    *Section
	Reason string
}

// AnalyzeReach computes the live section set.
//
// Roots (indirect roots participate explicitly):
//  1. the section defining the entry symbol,
//  2. every Keep-marked section (data-like anchor),
//  3. the section defining every exported symbol.
//
// Traversal follows relocations; weak-undefined references with no
// definition add no edge (they resolve to null).
func (p *Program) AnalyzeReach() *ReachResult {
	r := &ReachResult{
		Kept:  map[SectionID]bool{},
		Edges: map[SectionID][]SectionID{},
	}
	addRoot := func(gs *Section, reason string) {
		if gs == nil {
			return
		}
		if !r.Kept[gs.ID] {
			r.Roots = append(r.Roots, rootReason{gs, reason})
		}
		r.Kept[gs.ID] = true
	}

	if entry := p.Globals[p.Entry]; entry != nil && entry.WinDef != nil {
		addRoot(entry.WinDef, "entry:"+p.Entry)
	}
	for _, gs := range p.Order {
		if gs.Keep {
			addRoot(gs, "keep")
		}
	}
	for _, g := range p.Globals {
		if g.Export && g.WinDef != nil {
			addRoot(g.WinDef, "export:"+g.Name)
		}
	}

	// Section edges from relocations.
	for _, gs := range p.Order {
		for _, rl := range gs.Relocs {
			var to *Section
			switch {
			case rl.Local != nil:
				to = rl.Local
			case rl.Target != nil && rl.Target.WinDef != nil:
				to = rl.Target.WinDef
			}
			if to != nil {
				r.Edges[gs.ID] = append(r.Edges[gs.ID], to.ID)
			}
		}
	}

	// Worklist closure.
	work := make([]SectionID, 0, len(r.Kept))
	for id := range r.Kept {
		work = append(work, id)
	}
	for len(work) > 0 {
		id := work[len(work)-1]
		work = work[:len(work)-1]
		for _, to := range r.Edges[id] {
			if !r.Kept[to] {
				r.Kept[to] = true
				work = append(work, to)
			}
		}
	}

	for _, gs := range p.Order {
		gs.Reachable = r.Kept[gs.ID]
		if !gs.Reachable {
			r.Dropped = append(r.Dropped, gs)
		}
	}
	return r
}

// UndefinedDiagnostics returns undefined-symbol errors restricted to
// references from reachable sections. Weak-undefined references are
// permitted to remain unresolved (they resolve to null at runtime).
func (p *Program) UndefinedDiagnostics(rr *ReachResult) []*LinkError {
	var errs []*LinkError
	seen := map[string]bool{}
	names := make([]string, 0, len(p.Globals))
	for name := range p.Globals {
		names = append(names, name)
	}
	// Deterministic order via Program.Order iteration over relocs.
	for _, gs := range p.Order {
		if !rr.Kept[gs.ID] {
			continue
		}
		for _, rl := range gs.Relocs {
			g := rl.Target
			if g == nil || g.WinDef != nil {
				continue
			}
			weak := false
			for _, raw := range gs.Obj.Symbols {
				if raw.Name == g.Name && raw.Bind == objfmt.BindWeak {
					weak = true
				}
			}
			if weak || g.WeakRef {
				continue
			}
			key := g.Name + "@" + gs.Obj.Name + ":" + gs.Name
			if seen[key] {
				continue
			}
			seen[key] = true
			errs = append(errs, &LinkError{
				Kind: KindUndefinedSymbol,
				Msg:  "undefined symbol " + g.Name + " referenced from " + gs.Obj.Name + ":" + gs.Name,
			})
		}
	}
	return errs
}
