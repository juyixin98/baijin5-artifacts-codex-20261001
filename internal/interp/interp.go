// Package interp is the reference engine: a direct tree-walking interpreter
// for the same AST. Generators run in goroutines driven over channels, so
// their suspend/resume and finally semantics follow the host scheduler rather
// than the lowered state machine. The differential harness treats this engine
// as the semantic oracle and checks that ir+vm agrees with it action by
// action.
package interp

import (
	"sync"
	"sync/atomic"

	"genfsm/internal/ast"
	"genfsm/internal/semerr"
	"genfsm/internal/value"
)

type Limits struct {
	Fuel     int64
	MaxDepth int
}

func DefaultLimits() Limits { return Limits{Fuel: 2_000_000, MaxDepth: 400} }

// Interpreter holds the global function table and limits.
type Interpreter struct {
	prog   *ast.Program
	funcs  map[string]*ast.Func
	limits Limits
	genSeq atomic.Int64
}

func New(prog *ast.Program, limits Limits) *Interpreter {
	in := &Interpreter{prog: prog, limits: limits, funcs: map[string]*ast.Func{}}
	for _, f := range prog.Funcs {
		in.funcs[f.Name] = f
	}
	return in
}

func (in *Interpreter) RunMain() (value.Value, error) {
	main := in.funcs["main"]
	env := newScope(nil)
	v, ex := in.execFuncBody(main.Body, env)
	if ex.Ok() {
		return value.NullV(), semerr.New(semerr.KindCompute, semerr.CodeUserRaised, "uncaught exception %s: %s", ex.name, ex.msg)
	}
	return v, nil
}

// scope is a lexical variable environment.
type scope struct {
	parent *scope
	vars   map[string]*value.Value
	// yield is non-nil only while a generator function body executes on its
	// own goroutine. It is carried by the lexical scope rather than by a
	// package-level variable, so nested generator goroutines never alias it.
	yield func(value.Value) (value.Value, bool)
}

func newScope(parent *scope) *scope {
	return &scope{parent: parent, vars: map[string]*value.Value{}}
}

func (s *scope) yieldFn() func(value.Value) (value.Value, bool) {
	for cur := s; cur != nil; cur = cur.parent {
		if cur.yield != nil {
			return cur.yield
		}
	}
	return nil
}

func (s *scope) define(name string, v value.Value) {
	cp := v
	s.vars[name] = &cp
}

func (s *scope) lookup(name string) (*value.Value, bool) {
	for cur := s; cur != nil; cur = cur.parent {
		if p, ok := cur.vars[name]; ok {
			return p, true
		}
	}
	return nil, false
}

type excVal struct {
	name string
	msg  string
}

func (e excVal) Ok() bool { return e.name != "" }

func exf(name, msg string) excVal { return excVal{name: name, msg: msg} }

// frame identity is tracked per goroutine to reject reentrant resumes.
var runningGens sync.Map // *Gen -> struct{} while its body executes

// CallGen creates a newborn generator handle by function name (used by the
// differential harness and REPL; ordinary programs call gens from code).
func (in *Interpreter) CallGen(name string, args []value.Value) (value.Value, error) {
	fn, ok := in.funcs[name]
	if !ok {
		return value.NullV(), semerr.Input(semerr.CodeBadRequest, "unknown generator %q", name)
	}
	if !fn.IsGen {
		return value.NullV(), semerr.Input(semerr.CodeBadRequest, "%q is not a generator", name)
	}
	if len(args) != len(fn.Params) {
		return value.NullV(), semerr.Input(semerr.CodeBadRequest,
			"generator %q expects %d args, got %d", name, len(fn.Params), len(args))
	}
	return value.GenV(in.spawnGen(fn, args)), nil
}
