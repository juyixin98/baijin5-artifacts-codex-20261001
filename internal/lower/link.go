package lower

import "rlc/internal/ir"

// LinkProgram assembles compiled modules and generic specializations into a
// runnable IR program.
func LinkProgram(an *Analysis, mods []*CompiledModule, specs []*ir.Func) *ir.Program {
	p := &ir.Program{Modules: append([]string{}, an.Order...)}
	for _, cm := range mods {
		p.Funcs = append(p.Funcs, cm.Funcs...)
		p.Consts = append(p.Consts, cm.Consts...)
	}
	p.Funcs = append(p.Funcs, specs...)
	return p
}
