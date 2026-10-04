// Package vm executes the explicit state-machine IR.
//
// Every generator execution is a resumable Frame: its complete machine state
// is (pc, locals, stack, handlers). Suspension saves that state inside the
// Gen handle; close/throw inject signals into it. Cleanup (finally clauses,
// for-loop child close) is driven by an explicit signal-and-handler protocol
// rather than by the host call stack, so applicable cleanups run exactly
// once and resuming a running generator is detected mechanically.
package vm

import (
	"sync/atomic"

	"genfsm/internal/ir"
	"genfsm/internal/semerr"
	"genfsm/internal/value"
)

// Limits bound host resources (resource exhaustion -> RESOURCE errors).
type Limits struct {
	Fuel          int64
	MaxDepth      int
	MaxGenerators int64
}

func DefaultLimits() Limits {
	return Limits{Fuel: 2_000_000, MaxDepth: 400, MaxGenerators: 100_000}
}

// Machine is one program instance (global function table + limits + counters).
type Machine struct {
	prog   *ir.Program
	limits Limits
	genSeq atomic.Int64
	genN   atomic.Int64
}

func NewMachine(prog *ir.Program, limits Limits) *Machine {
	return &Machine{prog: prog, limits: limits}
}

func (m *Machine) routine(name string) *ir.Routine { return m.prog.Routines[name] }

// CallMain runs main() to completion and returns its value or a typed error.
func (m *Machine) CallMain() (value.Value, error) {
	r := m.routine("main")
	f := m.newFrame(r, nil)
	res := m.runFrame(f)
	if res.kind == sigThrow {
		return value.NullV(), semerr.New(semerr.KindCompute, semerr.CodeUserRaised, "uncaught exception %s: %s",
			res.ex.ExName, res.ex.ExMessage)
	}
	return res.val, nil
}

// signal kinds of the explicit unwind protocol.
type sigKind int

const (
	sigNormal sigKind = iota
	sigReturn
	sigThrow
	sigClose
	sigYield
	sigFatal
)

// signal is the pending continuation of an in-progress frame unwind.
type signal struct {
	kind sigKind
	val  value.Value     // return value (sigReturn) or thrown exception (sigThrow)
	ex   value.Value
	cont int             // continuation PC (sigReturn / normal finally)
}

// hEntry is one dynamic handler on the frame's explicit handler stack.
type hEntry struct {
	kind      ir.HandlerKind
	jCatch    int
	jFinally  int
	phase     int // 0 = body, 1 = cleanup
	paramSlot int
	genSlot   int
	pending   signal
	normalCont int
}

type frame struct {
	r        *ir.Routine
	pc       int
	locals   []value.Value
	stack    []value.Value
	handlers []*hEntry
	status   value.GenStatus
	// Pause bookkeeping.
	pauseID int
	yieldPC int // pc of the OYield that suspended the frame; -1 otherwise
	resume  *resumeIn
	resumeYieldPC int
	// closeCleanup is true while a CLOSE signal is running applicable cleanups.
	closeCleanup bool
	// fatal carries a non-language terminal failure (e.g. resuming a running
	// generator reached through user code). It aborts the whole resume.
	fatalKind string
	fatalCode string
	fatalMsg  string
}

type resumeIn struct {
	val value.Value
	ex  value.Value
	// throwIn true when resume carries an injected exception.
	throwIn bool
	closing  bool
}

func (m *Machine) newFrame(r *ir.Routine, args []value.Value) *frame {
	f := &frame{r: r, status: value.StatusNewborn}
	f.yieldPC = -1
	f.locals = make([]value.Value, r.NSlots)
	for i := range f.locals {
		f.locals[i] = value.NullV()
	}
	for i, p := range r.Params {
		_ = p
		if i < len(args) {
			f.locals[i] = args[i]
		}
	}
	return f
}

func (f *frame) push(v value.Value) { f.stack = append(f.stack, v) }

func (f *frame) pop() value.Value {
	n := len(f.stack)
	v := f.stack[n-1]
	f.stack = f.stack[:n-1]
	return v
}

func (f *frame) top() value.Value { return f.stack[len(f.stack)-1] }

func (f *frame) pushHandler(h *hEntry) { f.handlers = append(f.handlers, h) }

func (f *frame) popHandler() *hEntry {
	n := len(f.handlers)
	h := f.handlers[n-1]
	f.handlers = f.handlers[:n-1]
	return h
}

// frameResult is how runFrame terminates: yield (paused), return, throw, close-done.
type frameResult struct {
	kind sigKind
	val  value.Value
	ex   value.Value
	fatalKind, fatalCode, fatalMsg string
}

// NewGenByName creates a newborn generator handle by routine name.
func (m *Machine) NewGenByName(name string, args []value.Value) (value.Value, error) {
	r := m.routine(name)
	if r == nil {
		return value.NullV(), semerr.Input(semerr.CodeBadRequest, "unknown routine %q", name)
	}
	if !r.IsGen {
		return value.NullV(), semerr.Input(semerr.CodeBadRequest, "%q is not a generator", name)
	}
	if len(args) != len(r.Params) {
		return value.NullV(), semerr.Input(semerr.CodeBadRequest,
			"generator %q expects %d args, got %d", name, len(r.Params), len(args))
	}
	if m.genN.Load() >= m.limits.MaxGenerators {
		return value.NullV(), semerr.Resource(semerr.CodeGeneratorLimit, "generator limit exceeded")
	}
	nf := m.newFrame(r, args)
	g := m.newGen(nf)
	m.genN.Add(1)
	return value.GenV(g), nil
}
