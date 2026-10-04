package vm

import (
	"genfsm/internal/ir"
	"genfsm/internal/value"
)

// doCall handles OCall: generator callees produce a newborn generator value;
// plain callees are executed immediately as nested frames.
func (m *Machine) doCall(f *frame, op ir.Op, depth *int) frameResult {
	r := m.routine(op.S)
	if r == nil {
		ex := value.ExV("NameError", "unknown function "+op.S)
		if rr := m.unwind(f, signal{kind: sigThrow, ex: ex}); rr.kind != sigNormal {
			return rr
		}
		return frameResult{kind: sigThrow, ex: ex}
	}
	narg := int(op.I)
	args := make([]value.Value, narg)
	for i := narg - 1; i >= 0; i-- {
		args[i] = f.pop()
	}
	if r.IsGen {
		if m.genN.Load() >= m.limits.MaxGenerators {
			ex := value.ExV("ResourceError", "generator limit exceeded")
			if rr := m.unwind(f, signal{kind: sigThrow, ex: ex}); rr.kind != sigNormal {
				return rr
			}
			return frameResult{kind: sigThrow, ex: ex}
		}
		nf := m.newFrame(r, args)
		g := m.newGen(nf)
		m.genN.Add(1)
		f.push(value.GenV(g))
		return frameResult{kind: sigNormal}
	}
	*depth++
	nf := m.newFrame(r, args)
	rr := m.runWith(nf, depth)
	*depth--
	switch rr.kind {
	case sigReturn:
		f.push(rr.val)
		return frameResult{kind: sigNormal}
	case sigYield:
		// plain routine cannot yield; defensive
		return frameResult{kind: sigThrow, ex: value.ExV("InternalError", "plain routine yielded")}
	default:
		// Propagate the child termination through this frame's handlers.
		sig := signal{kind: rr.kind, val: rr.val, ex: rr.ex}
		if pr := m.unwind(f, sig); pr.kind != sigNormal {
			return pr
		}
		return frameResult{kind: rr.kind, val: rr.val, ex: rr.ex}
	}
}

// doIterNext drives one for-loop iteration:
//
//	g.Next() -> yielded value pushed; finished jumps to J1.
//	Exceptions from the child unwind through this frame's handlers.
func (m *Machine) doIterNext(f *frame, op ir.Op, depth *int) frameResult {
	gv := f.locals[op.I]
	g := gv.Gen
	res := g.Next()
	switch res.State {
	case value.StateYielded:
		f.push(res.Value)
		return frameResult{kind: sigNormal}
	case value.StateFinished:
		f.pc = op.J1
		return frameResult{kind: sigNormal}
	case value.StateClosed:
		f.pc = op.J1
		return frameResult{kind: sigNormal}
	case value.StateError:
		if res.ErrKind == "CONFLICT" {
			// Re-entrant resume of a running generator is a hard state
			// conflict: abort the whole resume with the original category.
			return frameResult{kind: sigFatal,
				fatalKind: res.ErrKind, fatalCode: res.ErrCode, fatalMsg: res.ErrMsg}
		}
		ex := value.ExV(res.ExName, res.ExMessage)
		if rr := m.unwind(f, signal{kind: sigThrow, ex: ex}); rr.kind != sigNormal {
			return rr
		}
		return frameResult{kind: sigThrow, ex: ex}
	}
	return frameResult{kind: sigNormal}
}
