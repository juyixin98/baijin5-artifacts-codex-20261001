// Package ir defines the lowered ScopeLang instruction set.
//
// The lowering turns lexical scopes into shared cleanup sections:
//
//	* every acquired resource owns one boolean guard slot;
//	* each scope has one cleanup section reached by every unwind reason
//	  (normal fall-through, break, return, init/explict failure);
//	* GuardCheck+GuardClear make each resource's cleanup idempotent, so a
//	  section executed via the shared path can never destroy twice;
//	* Route dispatches on the unwind reason after cleanups have run.
package ir

import (
	"fmt"
	"strings"
)

type Op string

const (
	OpEmit         Op = "emit"
	OpAcquire      Op = "acquire"
	OpUnwind       Op = "unwind"       // explicit fail/break/return -> set reason, jump
	OpCleanupFail  Op = "cleanup_fail" // fail inside a cleanup: sticky, no jump
	OpSetCounter      Op = "set_counter"
	OpIterEnter       Op = "iter_enter"
	OpIterLeave       Op = "iter_leave"
	OpCounterCheck    Op = "counter_check" // jump target when counter <= 0
	OpCounterDecr     Op = "counter_decr"
	OpResetGuards  Op = "reset_guards"
	OpGuardCheck   Op = "guard_check" // jump target when slot is clear
	OpGuardClear   Op = "guard_clear"
	OpSetReason    Op = "set_reason"
	OpJmp          Op = "jmp"
	OpRoute        Op = "route"
	OpBreakAbsorb  Op = "break_absorb"
	OpHaltOK       Op = "halt_ok"
	OpHaltReturn   Op = "halt_return"
	OpHaltFail     Op = "halt_fail"
)

// Reason is the current unwind mode carried through cleanup sections.
type Reason string

const (
	RNormal Reason = "normal"
	RBreak  Reason = "break"
	RReturn Reason = "return"
	RFail   Reason = "fail"
)

type Instruction struct {
	Op     Op
	Text   string // emit text
	Point  string // init@N / fail@N / close@N
	Name   string // acquire resource name
	Expr   string // acquire resource expression
	Slot   int    // guard slot
	Slots  []int  // reset_guards explicit slot list
	Count  int    // counter initial value / iter_enter total
	Temp   int    // counter temp slot
	Reason Reason // unwind/set_reason operand
	Value  string // return value
	Target string // single jump/guard target
	Normal string // route targets
	Break  string
	Return string
	Fail   string
	Line   int
}

// PointInfo describes a stable injection point for tooling and configs.
type PointInfo struct {
	Point    string `json:"point"`
	Kind     string `json:"kind"` // init | close | fail
	Ordinal  int    `json:"ordinal"`
	Name     string `json:"name,omitempty"`
	Expr     string `json:"expr,omitempty"`
	Line     int    `json:"line"`
	InRepeat bool   `json:"in_repeat,omitempty"`
}

type Program struct {
	Instructions []Instruction `json:"instructions"`
	Labels       map[string]int `json:"labels"`
	Points       []PointInfo   `json:"points"`
	Slots        int           `json:"slots"`
	Temps        int           `json:"temps"`
}

// Listing renders an assembly-style listing used by `sf lower` and logs.
func (p *Program) Listing() string {
	labelsAt := map[int][]string{}
	for name, idx := range p.Labels {
		labelsAt[idx] = append(labelsAt[idx], name)
	}
	var b strings.Builder
	for i, ins := range p.Instructions {
		if labs := labelsAt[i]; len(labs) > 0 {
			for _, l := range sortedLabs(labs) {
				fmt.Fprintf(&b, "%s:\n", l)
			}
		}
		fmt.Fprintf(&b, "%3d  %s\n", i, renderIns(ins))
	}
	return b.String()
}

func renderIns(ins Instruction) string {
	switch ins.Op {
	case OpEmit:
		return fmt.Sprintf("emit %q", ins.Text)
	case OpAcquire:
		return fmt.Sprintf("acquire slot=%d point=%s name=%s expr=%q -> %s",
			ins.Slot, ins.Point, ins.Name, ins.Expr, ins.Target)
	case OpUnwind:
		if ins.Reason == RReturn {
			return fmt.Sprintf("unwind reason=return value=%q -> %s", ins.Value, ins.Target)
		}
		return fmt.Sprintf("unwind reason=%s point=%s -> %s", ins.Reason, ins.Point, ins.Target)
	case OpCleanupFail:
		return fmt.Sprintf("cleanup_fail point=%s", ins.Point)
	case OpSetCounter:
		return fmt.Sprintf("set_counter t%d = %d", ins.Temp, ins.Count)
	case OpIterEnter:
		return "iter_enter"
	case OpIterLeave:
		return "iter_leave"
	case OpCounterCheck:
		return fmt.Sprintf("counter_check t%d -> %s", ins.Temp, ins.Target)
	case OpCounterDecr:
		return fmt.Sprintf("counter_decr t%d", ins.Temp)
	case OpResetGuards:
		return fmt.Sprintf("reset_guards %v", ins.Slots)
	case OpGuardCheck:
		return fmt.Sprintf("guard_check slot=%d -> %s", ins.Slot, ins.Target)
	case OpGuardClear:
		return fmt.Sprintf("guard_clear slot=%d", ins.Slot)
	case OpSetReason:
		return fmt.Sprintf("set_reason %s", ins.Reason)
	case OpJmp:
		return "jmp " + ins.Target
	case OpRoute:
		return fmt.Sprintf("route normal=%s break=%s return=%s fail=%s",
			ins.Normal, ins.Break, ins.Return, ins.Fail)
	case OpBreakAbsorb:
		return fmt.Sprintf("break_absorb normal=%s after=%s return=%s fail=%s",
			ins.Normal, ins.Break, ins.Return, ins.Fail)
	case OpHaltOK:
		return "halt_ok"
	case OpHaltReturn:
		return "halt_return"
	case OpHaltFail:
		return "halt_fail"
	default:
		return string(ins.Op)
	}
}

func sortedLabs(labs []string) []string {
	out := append([]string(nil), labs...)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j-1] > out[j]; j-- {
			out[j-1], out[j] = out[j], out[j-1]
		}
	}
	return out
}
