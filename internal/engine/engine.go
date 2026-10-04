// Package engine defines the front-door contract used by the CLI and the
// differential test harness: parse once, then obtain two interchangeable
// execution engines over identical source. All cross-module failures are
// *semerr.Error values with a stable Kind/Code.
package engine

import (
	"genfsm/internal/ast"
	"genfsm/internal/interp"
	"genfsm/internal/ir"
	"genfsm/internal/parser"
	"genfsm/internal/value"
	"genfsm/internal/vm"
)

// Config carries resource limits shared by both engines.
type Config struct {
	Fuel          int64
	MaxDepth      int
	MaxGenerators int64
}

func DefaultConfig() Config {
	return Config{Fuel: 2_000_000, MaxDepth: 400, MaxGenerators: 100_000}
}

// Bundle is the parsed + compiled program exposed to callers.
type Bundle struct {
	AST *ast.Program
	IR  *ir.Program

	Source string
}

// Frontend parses and statically validates source (Kind=INPUT failures).
func Frontend(src string) (*Bundle, error) {
	ap, err := parser.Parse(src)
	if err != nil {
		return nil, err
	}
	ip, err := ir.Compile(ap)
	if err != nil {
		return nil, err
	}
	return &Bundle{AST: ap, IR: ip, Source: src}, nil
}

// Engine selects an execution strategy.
type Engine string

const (
	EngineFSM  Engine = "fsm"  // lowered explicit state machine (ir + vm)
	EngineRef  Engine = "ref"  // direct tree-walking reference interpreter
)

// Runner drives one execution engine.
type Runner interface {
	Name() Engine
	// RunMain executes main() to completion.
	RunMain() (value.Value, error)
	// NewGen creates a newborn generator handle by name.
	NewGen(name string, args []value.Value) (value.Gen, error)
}

// New builds a runner for the chosen engine.
func New(b *Bundle, e Engine, cfg Config) (Runner, error) {
	switch e {
	case EngineFSM:
		lim := vm.Limits{Fuel: cfg.Fuel, MaxDepth: cfg.MaxDepth, MaxGenerators: cfg.MaxGenerators}
		return &fsmRunner{m: vm.NewMachine(b.IR, lim)}, nil
	case EngineRef:
		lim := interp.Limits{Fuel: cfg.Fuel, MaxDepth: cfg.MaxDepth}
		return &refRunner{i: interp.New(b.AST, lim)}, nil
	}
	return nil, errUnknownEngine(e)
}

type fsmRunner struct{ m *vm.Machine }

func (r *fsmRunner) Name() Engine { return EngineFSM }
func (r *fsmRunner) RunMain() (value.Value, error) { return r.m.CallMain() }
func (r *fsmRunner) NewGen(name string, args []value.Value) (value.Gen, error) {
	v, err := r.m.NewGenByName(name, args)
	if err != nil {
		return nil, err
	}
	return v.Gen, nil
}

type refRunner struct{ i *interp.Interpreter }

func (r *refRunner) Name() Engine { return EngineRef }
func (r *refRunner) RunMain() (value.Value, error) { return r.i.RunMain() }
func (r *refRunner) NewGen(name string, args []value.Value) (value.Gen, error) {
	v, err := r.i.CallGen(name, args)
	if err != nil {
		return nil, err
	}
	return v.Gen, nil
}
