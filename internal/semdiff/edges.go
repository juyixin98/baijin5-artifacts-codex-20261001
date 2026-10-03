package semdiff

import (
	"sort"

	"rlmod/internal/ir"
)

// buildEdges constructs the "depends on" graph of the new program. An edge
// from A -> B means B invalidates A when B changes.
//
// Edge kinds:
//
//	caller function       -> callee function        (call_deps)
//	caller function       -> generic instantiation  (generic_deps)
//	inlining function     -> const                  (const_deps)
//	every module symbol   -> public types in modules the module imports
//
// The graph is built from the fingerprint dependency bookkeeping recorded
// during lowering (not re-derived from source), so it reflects exactly what
// the compiled units embedded.
func buildEdges(prog *ir.Program) []edge {
	var edges []edge
	seen := map[[2]string]bool{}
	// edge{from: dependency, to: consumer}: when `from` changes, `to` is
	// invalidated.
	add := func(consumer, dependency string) {
		if consumer == dependency {
			return
		}
		k := [2]string{dependency, consumer}
		if seen[k] {
			return
		}
		seen[k] = true
		edges = append(edges, edge{from: dependency, to: consumer})
	}

	for _, mname := range prog.Order {
		mod := prog.Modules[mname]
		for _, fkey := range sortedFunc(mod) {
			f := mod.Funcs[fkey]
			self := nodeID(mname, f.Key)
			for _, dep := range f.CallDeps() {
				add(self, dep)
			}
			for _, dep := range f.GenericDeps() {
				add(self, dep)
			}
			for _, dep := range f.ConstDeps() {
				add(self, dep)
			}
		}
		// module-level: a change to an imported module's exported type is a
		// signature hazard for every function in this module that names that
		// type in its signature. Keep it precise: connect functions whose
		// params/results mention the imported named type.
		for _, fkey := range sortedFunc(mod) {
			f := mod.Funcs[fkey]
			self := nodeID(mname, f.Key)
			for _, t := range signatureTypes(f) {
				if t.Module != "" && t.Module != mname {
					add(self, nodeID(t.Module, t.Name))
				}
			}
		}
	}
	return edges
}

func signatureTypes(f *ir.Func) []ir.TypeRef {
	var out []ir.TypeRef
	for _, p := range f.Params {
		out = append(out, p.Type)
	}
	if f.HasResult {
		out = append(out, f.Result)
	}
	return out
}

func sortedFunc(mod *ir.Module) []string {
	out := make([]string, 0, len(mod.Funcs))
	for k := range mod.Funcs {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
