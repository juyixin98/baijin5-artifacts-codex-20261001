package runtime

import (
	"genstatemachine/internal/gerr"
	"genstatemachine/internal/ir"
)

// request describes one driver operation handed to the run loop.
type request struct {
	kind   reqKind
	inject Value
}

type reqKind int

const (
	reqNext reqKind = iota
	reqThrow
	reqClose
)

// Next implements Generator.
func (m *Machine) Next() Outcome { return m.drive(request{kind: reqNext}) }

// Throw implements Generator.
func (m *Machine) Throw(v Value) Outcome { return m.drive(request{kind: reqThrow, inject: v}) }

// Close implements Generator.
func (m *Machine) Close() Outcome { return m.drive(request{kind: reqClose}) }

func (r request) label() string {
	switch r.kind {
	case reqThrow:
		return "throw"
	case reqClose:
		return "close"
	default:
		return "next"
	}
}

func (m *Machine) drive(req request) Outcome {
	if bad := m.beginRequest(req.label()); bad != nil {
		return *bad
	}
	m.logs = nil

	switch req.kind {
	case reqNext:
		// Resume from Created/Suspended at pc normally.
	case reqThrow:
		m.pending = &abrupt{kind: akRaised, value: req.inject}
		if !m.route() {
			return m.settlePending()
		}
	case reqClose:
		if m.state == Created {
			// No cleanup has been entered yet, so there is nothing
			// applicable to run.
			m.setState(Closed)
			return Outcome{Kind: OutClosedOK, State: Closed}
		}
		m.pending = &abrupt{kind: akClosing}
		if !m.route() {
			return m.settlePending()
		}
	}

	for {
		if m.steps >= m.budget {
			m.pending = &abrupt{kind: akBudget, code: gerr.RStepBudget,
				msg: gerr.New(gerr.RStepBudget, "step budget %d exhausted", m.budget).Error()}
			if !m.route() {
				return m.settlePending()
			}
			continue
		}
		if m.pc < 0 || m.pc >= len(m.prog.Blocks) {
			return m.settleInternal(gerr.CUncaughtExit, "control fell off the CFG")
		}
		block := m.prog.Blocks[m.pc]

		stop, out := m.execBlockOps(block)
		if stop {
			return out
		}

		switch block.Term.Kind {
		case ir.TermYield:
			m.steps++
			return m.settleYield(block.Term)
		case ir.TermHalt:
			m.steps++
			if m.pending != nil {
				return m.settlePending()
			}
			m.setState(Done)
			return Outcome{Kind: OutExhausted, State: Done, Logs: m.logs}
		case ir.TermReturn:
			m.steps++
			var v Value
			has := false
			if block.Term.Reg >= 0 && block.Term.Reg < len(m.regs) {
				v = m.regs[block.Term.Reg]
				has = true
			}
			ab := &abrupt{kind: akReturn}
			if has {
				ab.value = v
			}
			m.pending = ab
			if !m.route() {
				return m.settlePending()
			}
		case ir.TermRaise:
			m.steps++
			v := NilVal()
			if block.Term.Reg >= 0 && block.Term.Reg < len(m.regs) {
				v = m.regs[block.Term.Reg]
			}
			if v.Kind != VStr {
				ge := gerr.New(gerr.CTypeMismatch, "throw requires a string exception value, got %s", v.Display())
				m.pending = &abrupt{kind: akFailure, code: ge.Code, msg: ge.Error()}
			} else {
				m.pending = &abrupt{kind: akRaised, value: v}
			}
			if !m.route() {
				return m.settlePending()
			}
		case ir.TermExit:
			m.steps++
			m.pending = &abrupt{kind: akClosing}
			if !m.route() {
				return m.settlePending()
			}

		case ir.TermJump:
			m.noteNormalEntry(block.Term.Target)
			m.gotoBlock(block.Term.Target)
		case ir.TermBranch:
			v := m.regs[block.Term.Reg]
			if v.Truthy() {
				m.gotoBlock(block.Term.Target)
			} else {
				m.gotoBlock(block.Term.Other)
			}
		default:
			return m.settleInternal(gerr.CUncaughtExit, "block %q has no terminator", block.Name)
		}
	}
}

// execBlockOps runs all flat operations in the current block, honoring an
// injected budget/failure between steps.
func (m *Machine) execBlockOps(block *ir.Block) (bool, Outcome) {
	for m.opIdx < len(block.Ops) {
		if m.steps >= m.budget {
			m.pending = &abrupt{kind: akBudget, code: gerr.RStepBudget,
				msg: gerr.New(gerr.RStepBudget, "step budget %d exhausted", m.budget).Error()}
			if !m.route() {
				return true, m.settlePending()
			}
			return false, Outcome{}
		}
		stop, out := m.execOp(block.Ops[m.opIdx])
		m.steps++
		m.opIdx++
		if stop {
			return true, out
		}
		// A compute failure inside an op routed into a finally block; the
		// current block is abandoned, so restart the outer loop.
		if m.pc != blockIndex(m.prog, block) {
			return false, Outcome{}
		}
	}
	return false, Outcome{}
}

func blockIndex(g *ir.GenIR, b *ir.Block) int {
	for i, bb := range g.Blocks {
		if bb == b {
			return i
		}
	}
	return -1
}

func (m *Machine) gotoBlock(idx int) {
	m.pc = idx
	m.opIdx = 0
}

// noteNormalEntry marks jumps that transfer into a finally block while there
// is no in-flight abrupt, so EndFinally knows cleanup was entered normally.
func (m *Machine) noteNormalEntry(target int) {
	m.normalFin = false
	if m.pending != nil {
		return
	}
	if n := len(m.handlers); n > 0 {
		top := &m.handlers[n-1]
		if target == top.h.FinallyBlock {
			m.normalFin = true
		}
	}
}
