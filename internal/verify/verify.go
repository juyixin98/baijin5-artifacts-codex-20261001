// Package verify validates a lowered ir.Program structurally. A malformed IR
// program indicates a compiler defect, so failures are Internal. The set of
// checks is intentionally exact and unit-tested.
package verify

import (
	"fmt"
	"strconv"

	"scopelang/internal/diag"
	"scopelang/internal/ir"
)

func Program(p *ir.Program) error {
	if p == nil {
		return internalErr("nil program")
	}
	if len(p.Instructions) == 0 {
		return internalErr("empty instruction stream")
	}
	last := p.Instructions[len(p.Instructions)-1]
	// Terminal layout produced by lower.Compile.
	if len(p.Instructions) < 3 {
		return internalErr("missing terminal halt instructions")
	}
	tail := p.Instructions[len(p.Instructions)-3:]
	if tail[0].Op != ir.OpHaltOK || tail[1].Op != ir.OpHaltReturn || tail[2].Op != ir.OpHaltFail {
		return internalErr("terminal instructions must be halt_ok, halt_return, halt_fail")
	}
	_ = last
	for i, in := range p.Instructions {
		if err := verifyIns(i, in, len(p.Instructions), p.Slots, p.Temps); err != nil {
			return err
		}
	}
	for name, idx := range p.Labels {
		if idx < 0 || idx >= len(p.Instructions) {
			return internalErr(fmt.Sprintf("label %s out of range: %d", name, idx))
		}
	}
	return nil
}

func verifyIns(i int, in ir.Instruction, nIns, slots, temps int) error {
	bad := func(msg string) error {
		return internalErr(fmt.Sprintf("ins %d (%s): %s", i, in.Op, msg))
	}
	checkTarget := func(t string) error {
		if t == "" {
			return nil
		}
		n, err := strconv.Atoi(t)
		if err != nil {
			return bad("non-numeric target " + t)
		}
		if n < 0 || n >= nIns {
			return bad(fmt.Sprintf("target %d out of range", n))
		}
		return nil
	}
	checkSlot := func(slot int) error {
		if slot < 0 || slot >= slots {
			return bad(fmt.Sprintf("slot %d out of range (have %d)", slot, slots))
		}
		return nil
	}
	checkTemp := func(t int) error {
		if t < 0 || t >= temps {
			return bad(fmt.Sprintf("temp %d out of range (have %d)", t, temps))
		}
		return nil
	}
	switch in.Op {
	case ir.OpAcquire:
		if err := checkSlot(in.Slot); err != nil {
			return err
		}
		if in.Point == "" || in.Name == "" {
			return bad("acquire needs point and name")
		}
		if err := checkTarget(in.Target); err != nil {
			return err
		}
	case ir.OpEmit, ir.OpCleanupFail:
		if in.Op == ir.OpCleanupFail && in.Point == "" {
			return bad("cleanup_fail needs point")
		}
	case ir.OpUnwind:
		switch in.Reason {
		case ir.RFail, ir.RBreak, ir.RReturn:
		default:
			return bad("invalid unwind reason")
		}
		if err := checkTarget(in.Target); err != nil {
			return err
		}
	case ir.OpGuardCheck:
		if err := checkSlot(in.Slot); err != nil {
			return err
		}
		if err := checkTarget(in.Target); err != nil {
			return err
		}
	case ir.OpGuardClear:
		if err := checkSlot(in.Slot); err != nil {
			return err
		}
	case ir.OpResetGuards:
		if len(in.Slots) == 0 {
			return bad("reset_guards needs slots")
		}
		for _, s := range in.Slots {
			if err := checkSlot(s); err != nil {
				return err
			}
		}
	case ir.OpSetCounter:
		if err := checkTemp(in.Temp); err != nil {
			return err
		}
		if in.Count < 0 {
			return bad("counter must be non-negative")
		}
	case ir.OpIterEnter:
		if err := checkTemp(in.Temp); err != nil {
			return err
		}
		if in.Count < 0 {
			return bad("iteration total must be non-negative")
		}
	case ir.OpIterLeave:
		// no operands
	case ir.OpCounterCheck, ir.OpCounterDecr:
		if err := checkTemp(in.Temp); err != nil {
			return err
		}
		if in.Op == ir.OpCounterCheck {
			if err := checkTarget(in.Target); err != nil {
				return err
			}
		}
	case ir.OpJmp:
		if err := checkTarget(in.Target); err != nil {
			return err
		}
	case ir.OpRoute, ir.OpBreakAbsorb:
		for _, t := range []string{in.Normal, in.Break, in.Return, in.Fail} {
			if err := checkTarget(t); err != nil {
				return err
			}
		}
	case ir.OpHaltOK, ir.OpHaltReturn, ir.OpHaltFail:
		// fine anywhere (only the tail matters structurally)
	default:
		return bad("unknown opcode")
	}
	return nil
}

func internalErr(msg string) *diag.Error {
	return diag.New(diag.Internal, "BAD_IR", msg).With(diag.PhaseVerify, "", 0)
}
