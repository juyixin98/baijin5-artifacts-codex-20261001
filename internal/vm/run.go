package vm

import (
	"genfsm/internal/ir"
	"genfsm/internal/value"
)

// runFrame executes f until it yields, returns, throws, closes or exhausts a
// resource. Fuel and depth are machine-wide counters.
func (m *Machine) runFrame(f *frame) frameResult {
	depth := 1
	return m.runWith(f, &depth)
}

func (m *Machine) runWith(f *frame, depth *int) frameResult {
	var fuel int64 = m.limits.Fuel
	for {
		// At a paused yield the frame resumes exactly at the instruction after
		// OYIELD; the resume payload is consumed by the dedicated resumeYield
		// block below before the normal fetch loop continues.
		if f.yieldPC >= 0 {
			ypc := f.yieldPC
			f.yieldPC = -1
			in := f.resume
			if in == nil {
				in = &resumeIn{}
			}
			f.resume = nil
			f.pc = ypc + 1
			if in.closing {
				// Start close unwind. A handler that runs cleanup rewrites pc
				// and returns sigNormal: fall through to the fetch loop so the
				// applicable finally body executes. Only with no handler left
				// does the frame terminate as closed.
				f.closeCleanup = true
				if r := m.unwind(f, signal{kind: sigClose}); r.kind != sigNormal {
					return r
				}
			} else if in.throwIn {
				ex := in.val
				if ex.Tag != value.Exception {
					ex = value.ExV("ThrowError", "injected non-exception")
				}
				if r := m.unwind(f, signal{kind: sigThrow, ex: ex}); r.kind != sigNormal {
					return r
				}
			} else {
				f.push(in.val)
			}
		}
		if *depth > m.limits.MaxDepth {
			return frameResult{kind: sigThrow, ex: value.ExV("ResourceError", "call depth exceeded")}
		}
		if fuel <= 0 {
			return frameResult{kind: sigThrow, ex: value.ExV("ResourceError", "out of fuel")}
		}
		fuel--
		if f.pc >= len(f.r.Code) {
			return frameResult{kind: sigReturn, val: value.NullV()}
		}
		op := f.r.Code[f.pc]
		f.pc++
		switch op.C {
		case ir.ONop:
		case ir.OConstNull:
			f.push(value.NullV())
		case ir.OConstBool:
			f.push(value.BoolV(f.r.NumLits.Bools[op.I]))
		case ir.OConstInt:
			f.push(value.IntV(f.r.NumLits.Ints[op.I]))
		case ir.OConstStr:
			f.push(value.StrV(f.r.NumLits.Strs[op.I]))
		case ir.OLoad:
			f.push(f.locals[op.I])
		case ir.OStore:
			f.locals[op.I] = f.pop()
		case ir.OPop:
			f.pop()
		case ir.OUnary:
			x := f.pop()
			v, ex := evalUnary(op.S, x)
			if ex.Ok() {
				if r := m.throwEx(f, ex); r.kind != sigNormal {
					return r
				}
				continue
			}
			f.push(v)
		case ir.OBinary:
			y := f.pop()
			x := f.pop()
			v, ex := evalBinary(op.S, x, y)
			if ex.Ok() {
				if r := m.throwEx(f, ex); r.kind != sigNormal {
					return r
				}
				continue
			}
			f.push(v)
		case ir.OBranchFalse:
			cond := f.pop()
			if !cond.IsTruthy() {
				f.pc = op.J1
			}
		case ir.OBranchTrue:
			cond := f.pop()
			if cond.IsTruthy() {
				f.pc = op.J1
			}
		case ir.OJump:
			f.pc = op.J1
		case ir.OReturn:
			v := f.pop()
			if r := m.unwind(f, signal{kind: sigReturn, val: v}); r.kind != sigNormal {
				return r
			}
			// no handlers: frame completes normally
			return frameResult{kind: sigReturn, val: v}
		case ir.OThrow:
			v := f.pop()
			if v.Tag != value.Exception {
				v = value.ExV("ThrowError", "throw requires an exception value")
			}
			if r := m.unwind(f, signal{kind: sigThrow, ex: v}); r.kind != sigNormal {
				return r
			}
			return frameResult{kind: sigThrow, ex: v}
		case ir.OCall:
			r2 := m.doCall(f, op, depth)
			if r2.kind != sigNormal {
				return r2
			}
		case ir.OYield:
			if f.closeCleanup {
				return frameResult{kind: sigThrow, ex: value.ExV("RuntimeError", "yield during generator close")}
			}
			// Pause half (only reached on first arrival): save the yielded
			// value and this pc; resume never re-executes OYIELD.
			yv := f.pop()
			f.yieldPC = f.pc - 1
			f.pauseID++
			return frameResult{kind: sigYield, val: yv}
		case ir.OSetup:
			f.pushHandler(&hEntry{kind: ir.HandlerTry, jCatch: op.J1, jFinally: op.J2, phase: 0, paramSlot: int(op.I)})
		case ir.OPopHandler:
			f.popHandler()
		case ir.OBindCatch:
			// entered via catch label: pop handler, move to cleanup phase
			h := f.popHandler()
			h.phase = 1
			f.pushHandler(h)
			ex := f.pop()
			f.locals[h.paramSlot] = ex
		case ir.OEnterFinally:
			// normal completion: bind continuation end, fall into finally body
			h := f.handlers[len(f.handlers)-1]
			h.phase = 1
			h.normalCont = op.J1
		case ir.OEnterFinallyUnwind:
			// entered via finally label during unwind; continuation already on h
		case ir.OEndFinally:
			h := f.handlers[len(f.handlers)-1]
			f.popHandler()
			switch h.pending.kind {
			case sigReturn:
				v := h.pending.val
				if r := m.unwind(f, signal{kind: sigReturn, val: v}); r.kind != sigNormal {
					return r
				}
				return frameResult{kind: sigReturn, val: v}
			case sigThrow:
				ex := h.pending.ex
				if r := m.unwind(f, signal{kind: sigThrow, ex: ex}); r.kind != sigNormal {
					return r
				}
				return frameResult{kind: sigThrow, ex: ex}
			case sigClose:
				if r := m.unwind(f, signal{kind: sigClose}); r.kind != sigNormal {
					return r
				}
				return frameResult{kind: sigClose}
			default:
				f.pc = h.normalCont
			}
		case ir.OSetupIter:
			gen := f.locals[op.I]
			if gen.Tag != value.Generator {
				return frameResult{kind: sigThrow, ex: value.ExV("TypeError", "for-loop target is not a generator")}
			}
			f.pushHandler(&hEntry{kind: ir.HandlerIter, genSlot: int(op.I), phase: 0})
		case ir.OPopIter:
			f.popHandler()
		case ir.OIterNext:
			if r := m.doIterNext(f, op, depth); r.kind != sigNormal {
				return r
			}
		default:
			return frameResult{kind: sigThrow, ex: value.ExV("InternalError", "bad opcode")}
		}
	}
}

// throwEx starts unwind with a freshly created language exception.
func (m *Machine) throwEx(f *frame, ex exceptVal) frameResult {
	if r := m.unwind(f, signal{kind: sigThrow, ex: value.ExV(ex.name, ex.msg)}); r.kind != sigNormal {
		return r
	}
	return frameResult{kind: sigNormal}
}

// inFinallyCleanup reports whether execution is currently running a finally
// entered because of close (yield is illegal there).
func (f *frame) inFinallyCleanup() (*hEntry, bool) {
	for i := len(f.handlers) - 1; i >= 0; i-- {
		h := f.handlers[i]
		if h.kind == ir.HandlerTry && h.phase == 1 {
			if h.pending.kind == sigClose {
				return h, true
			}
			return h, false
		}
	}
	return nil, false
}
