package runtime

import (
	"strings"

	"genstatemachine/internal/gerr"
	"genstatemachine/internal/ir"
)

// execOp executes one flat operation. It returns stop=true when the request
// has settled (failure); normal ops return false.
func (m *Machine) execOp(op ir.Op) (bool, Outcome) {
	switch op.Kind {
	case ir.OpNop:
		return false, Outcome{}

	case ir.OpConst:
		m.setReg(op.R, fromIR(op.Const))
		return false, Outcome{}

	case ir.OpLoad:
		v, ok := m.locals[op.Name]
		if !ok {
			return m.failCompute(gerr.CTypeMismatch, "read of undeclared variable %q", op.Name)
		}
		m.setReg(op.R, v)
		return false, Outcome{}

	case ir.OpStore:
		m.locals[op.Name] = m.reg(op.R1)
		return false, Outcome{}

	case ir.OpUnary:
		v := m.reg(op.R1)
		r, err := evalUnary(op.Op, v)
		if err != nil {
			return m.failOp(err, op.Line)
		}
		m.setReg(op.R, r)
		return false, Outcome{}

	case ir.OpBinary:
		l := m.reg(op.R1)
		r := m.reg(op.R2)
		res, err := evalBinary(op.Op, l, r)
		if err != nil {
			return m.failOp(err, op.Line)
		}
		m.setReg(op.R, res)
		return false, Outcome{}

	case ir.OpLog:
		parts := make([]string, 0, len(op.ArgRegs))
		for _, rr := range op.ArgRegs {
			parts = append(parts, m.reg(rr).Display())
		}
		line := strings.Join(parts, " ")
		m.logs = append(m.logs, line)
		m.setReg(op.R, NilVal())
		return false, Outcome{}

	case ir.OpBindCatch:
		m.locals[op.Name] = m.raised
		m.setReg(op.R, m.raised)
		return false, Outcome{}

	case ir.OpPushHandler:
		m.handlers = append(m.handlers, handler{h: op.Handler})
		return false, Outcome{}

	case ir.OpPopHandler:
		if n := len(m.handlers); n > 0 {
			m.handlers = m.handlers[:n-1]
		}
		return false, Outcome{}

	case ir.OpSetCatchMode:
		if n := len(m.handlers); n > 0 {
			m.handlers[n-1].inCatch = true
		}
		return false, Outcome{}

	case ir.OpSetFinallyMode:
		if n := len(m.handlers); n > 0 {
			m.handlers[n-1].inFin = true
		}
		return false, Outcome{}

	case ir.OpEndFinally:
		m.endFinally()
		return false, Outcome{}
	}
	return m.failCompute(gerr.CUncaughtExit, "unknown IR op %d", op.Kind)
}

// route resolves m.pending against the installed handler stack by moving to a
// catch/finally block. When nothing remains it returns false and the caller
// settles the pending abrupt directly.
func (m *Machine) route() bool {
	if m.pending == nil {
		return true
	}
	p := m.pending
	for len(m.handlers) > 0 {
		top := &m.handlers[len(m.handlers)-1]
		switch p.kind {
		case akBudget, akFailure:
			if top.inFin {
				m.handlers = m.handlers[:len(m.handlers)-1]
				continue
			}
			if top.h.FinallyBlock >= 0 {
				top.inFin = true
				m.gotoBlock(top.h.FinallyBlock)
				return true
			}
			m.handlers = m.handlers[:len(m.handlers)-1]
			continue
		case akClosing, akReturn:
			if top.inFin {
				m.handlers = m.handlers[:len(m.handlers)-1]
				continue
			}
			if top.h.FinallyBlock >= 0 {
				top.inFin = true
				m.gotoBlock(top.h.FinallyBlock)
				return true
			}
			m.handlers = m.handlers[:len(m.handlers)-1]
			continue
		case akRaised:
			if top.inFin {
				// A raise inside a finally body replaces that cleanup and
				// continues to outer handlers.
				m.handlers = m.handlers[:len(m.handlers)-1]
				continue
			}
			if !top.inCatch &&
				top.h.Kind == ir.HandlerCatch && top.h.CatchBlock >= 0 &&
				m.catchMatches(top) {
				top.inCatch = true
				m.raised = p.value
				m.gotoBlock(top.h.CatchBlock)
				m.pending = nil
				return true
			}
			if top.h.FinallyBlock >= 0 {
				top.inFin = true
				m.gotoBlock(top.h.FinallyBlock)
				return true
			}
			m.handlers = m.handlers[:len(m.handlers)-1]
			continue
		}
	}
	return false
}

func (m *Machine) catchMatches(top *handler) bool {
	if !top.h.HasFilter {
		return true
	}
	filter := m.reg(top.h.FilterReg)
	return filter.Equals(m.pending.value)
}

// endFinally re-dispatches the saved abrupt after a finally body completed.
// The current frame has already been popped. If an enclosing frame is itself
// mid-cleanup/catch, execution resumes inside that body; otherwise the abrupt
// unwinds further.

func (m *Machine) endFinally() bool {
	if m.pending == nil {
		// Normal fall-through into finally: nothing to re-dispatch.
		m.normalFin = false
		return false
	}
	m.normalFin = false
	if n := len(m.handlers); n > 0 {
		top := &m.handlers[n-1]
		if top.inFin || top.inCatch {
			return false
		}
	}
	if m.route() {
		return false
	}
	return true // orphan abrupt: caller settles
}

func (m *Machine) reg(i int) Value {
	if i < 0 || i >= len(m.regs) {
		return NilVal()
	}
	return m.regs[i]
}

func (m *Machine) setReg(i int, v Value) {
	for len(m.regs) <= i {
		m.regs = append(m.regs, NilVal())
	}
	m.regs[i] = v
}

func fromIR(v ir.Value) Value {
	return Value{Kind: ValueKind(v.Kind), I: v.I, S: v.S, B: v.B}
}

// failOp turns a built-in compute failure (divide by zero, bad type) into an
// uncatchable-but-cleanup-running abrupt, just like budget exhaustion.
func (m *Machine) failOp(err error, line int) (bool, Outcome) {
	ge, ok := gerr.As(err)
	if !ok {
		ge = gerr.New(gerr.CTypeMismatch, "%s", err.Error())
	}

	ge.Line = line
	m.pending = &abrupt{kind: akFailure, code: ge.Code, msg: ge.Error()}
	if !m.route() {
		return true, m.settlePending()
	}
	return false, Outcome{}
}

func (m *Machine) failCompute(code gerr.Code, format string, args ...any) (bool, Outcome) {

	ge := gerr.New(code, format, args...)
	m.pending = &abrupt{kind: akFailure, code: ge.Code, msg: ge.Error()}
	if !m.route() {
		return true, m.settlePending()
	}
	return false, Outcome{}
}
