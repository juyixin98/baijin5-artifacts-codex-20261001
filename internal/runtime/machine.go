package runtime

import (
	"sync"

	"genstatemachine/internal/gerr"
	"genstatemachine/internal/ir"
)

// abruptKind describes the in-flight control-flow reason while unwinding.
type abruptKind int

const (
	akRaised  abruptKind = iota // user-thrown exception
	akReturn                    // generator return
	akClosing                   // close requested GeneratorExit

	akBudget  // step budget exhausted (uncatchable)
	akFailure // built-in compute failure (div0/type), uncatchable
)

// abrupt is the pending control-flow signal carried during unwind.
type abrupt struct {
	kind  abruptKind
	value Value // for akRaised / akReturn
	code  gerr.Code
	msg   string
}

// Machine executes the explicit CFG produced by ir.Compile. It never parses
// or walks the AST: all behavior comes from the saved blocks, registers,
// locals and explicit handler stack.
type Machine struct {
	prog   *ir.GenIR
	mu     sync.Mutex
	state  State
	locals map[string]Value
	regs   []Value

	pc       int       // current block index
	opIdx    int       // next op inside block
	handlers []handler // explicit pending-cleanup stack
	raised   Value     // value injected/bound for the current raise

	pending   *abrupt // non-nil while unwinding through finally
	normalFin bool    // true when entering finally by normal fall-through
	unwindVal Value
	steps     int64
	budget    int64

	logs []string
}

// handler is the runtime mirror of ir.Handler with mutable mode.
type handler struct {
	h       ir.Handler
	inCatch bool
	inFin   bool
}

// NewMachine constructs a suspended-at-entry machine for g.
func NewMachine(g *ir.GenIR, budget int64) *Machine {
	if budget <= 0 {
		budget = DefaultStepBudget
	}
	return &Machine{
		prog:   g,
		state:  Created,
		locals: map[string]Value{},
		pc:     g.Entry,
		budget: budget,
	}
}

// DefaultStepBudget bounds the number of executed IR steps per request and
// overall, bounding infinite loops deterministically.
const DefaultStepBudget int64 = 100_000

func (m *Machine) Name() string { return m.prog.Name }

func (m *Machine) State() State {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.state
}

func (m *Machine) Snapshot() Snapshot {
	m.mu.Lock()
	defer m.mu.Unlock()
	snap := Snapshot{
		Name:     m.prog.Name,
		State:    m.state.String(),
		BlockIdx: m.pc,
		Handlers: len(m.handlers),
		Steps:    m.steps,
		Locals:   map[string]Value{},
	}
	if m.pc >= 0 && m.pc < len(m.prog.Blocks) {
		snap.Block = m.prog.Blocks[m.pc].Name
	}
	for k, v := range m.locals {
		snap.Locals[k] = v
	}
	return snap
}

// beginRequest performs the state precondition check and flips state to
// Running. It returns the outcome to return to the caller on violation.
func (m *Machine) beginRequest(op string) *Outcome {
	m.mu.Lock()
	defer m.mu.Unlock()
	switch m.state {
	case Running:
		fail := failOutcome(gerr.SReentry, "generator %q is already running; %s rejected", m.prog.Name, op)
		o := &fail
		o.State = Running
		return o
	case Created, Suspended:
		m.state = Running
		return nil
	case Closed:
		fail := failOutcome(gerr.SAlreadyDone, "generator %q is closed; %s rejected", m.prog.Name, op)
		o := &fail
		o.State = Closed
		return o
	case Done:
		fail := failOutcome(gerr.SAlreadyDone, "generator %q is done; %s rejected", m.prog.Name, op)
		o := &fail
		o.State = Done
		return o
	case Failed:
		fail := failOutcome(gerr.SAlreadyDone, "generator %q has failed; %s rejected", m.prog.Name, op)
		o := &fail
		o.State = Failed
		return o
	}
	return nil
}

func failOutcome(code gerr.Code, format string, args ...any) Outcome {
	e := gerr.New(code, format, args...)

	return Outcome{Kind: OutFailed, ErrCode: string(e.Code), ErrMsg: e.Message}
}

func (m *Machine) finishLocked(state State) {
	m.state = state
}
