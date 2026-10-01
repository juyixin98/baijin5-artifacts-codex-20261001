// Package interp implements a direct reference interpreter for ir.Program.
// It is deliberately independent from the partial evaluator and serves as the
// semantic oracle used by differential tests.
package interp

import (
	"fmt"

	"funcspect/internal/diag"
	"funcspect/internal/ir"
)

// Trace records observable effects and execution counters. Field outputs hold
// values passed to print(); reads are consumed from Inputs in order.
type Trace struct {
	Outputs  []int64
	Reads    int
	Steps    int
	MaxDepth int
	depth    int
}

// Inputs is the stream of integer values returned by read().
type Inputs []int64

// Result is the outcome of one run.
type Result struct {
	Value   int64
	Trace   Trace
	Outputs []int64
}

// Limits bounds interpreter resource use.
type Limits struct {
	MaxDepth int
	MaxSteps int
}

// DefaultLimits are conservative enough for tests.
func DefaultLimits() Limits {
	return Limits{MaxDepth: 10_000, MaxSteps: 5_000_000}
}

type env struct {
	parent *env
	vars   map[string]int64
}

func (e *env) lookup(name string) (int64, bool) {
	for cur := e; cur != nil; cur = cur.parent {
		if v, ok := cur.vars[name]; ok {
			return v, true
		}
	}
	return 0, false
}

// Run evaluates the entry function with static integer arguments.
func Run(prog *ir.Program, args []int64, inputs Inputs, limits Limits) (*Result, error) {
	entry, ok := prog.Funcs[ir.EntryName]
	if !ok {
		return nil, diag.New(diag.CatSyntax, fmt.Sprintf("missing entry %q", ir.EntryName))
	}
	if len(args) != entry.Arity() {
		return nil, diag.New(diag.CatArity, fmt.Sprintf("entry %s expects %d static args, got %d", ir.EntryName, entry.Arity(), len(args)))
	}
	root := &env{vars: map[string]int64{}}
	for i, p := range entry.Params {
		root.vars[p.Name] = args[i]
	}
	st := &state{prog: prog, inputs: inputs, limits: limits}
	v, err := st.eval(entry.Body, root)
	if err != nil {
		return nil, err
	}
	return &Result{Value: v, Trace: st.trace, Outputs: append([]int64(nil), st.trace.Outputs...)}, nil
}

type state struct {
	prog   *ir.Program
	inputs Inputs
	trace  Trace
	limits Limits
}

func (s *state) step() error {
	s.trace.Steps++
	if s.limits.MaxSteps > 0 && s.trace.Steps > s.limits.MaxSteps {
		return diag.New(diag.CatStackOverflow, "interpreter step limit exceeded")
	}
	return nil
}

func (s *state) callEnter() error {
	s.trace.depth++
	if s.trace.depth > s.trace.MaxDepth {
		s.trace.MaxDepth = s.trace.depth
	}
	if s.limits.MaxDepth > 0 && s.trace.depth > s.limits.MaxDepth {
		s.trace.depth--
		return diag.New(diag.CatStackOverflow, "call depth limit exceeded")
	}
	return nil
}

func (s *state) callLeave() { s.trace.depth-- }

func (s *state) eval(e ir.Expr, en *env) (int64, error) {
	if err := s.step(); err != nil {
		return 0, err
	}
	switch x := e.(type) {
	case *ir.Int:
		return x.Value, nil
	case *ir.Var:
		v, ok := en.lookup(x.Name)
		if !ok {
			return 0, diag.New(diag.CatUnresolved, fmt.Sprintf("unbound variable %q", x.Name))
		}
		return v, nil
	case *ir.Unary:
		v, err := s.eval(x.X, en)
		if err != nil {
			return 0, err
		}
		return evalUnary(x.Op, v)
	case *ir.Binary:
		return s.evalBinary(x, en)
	case *ir.If:
		c, err := s.eval(x.Cond, en)
		if err != nil {
			return 0, err
		}
		if c != 0 {
			return s.eval(x.Then, en)
		}
		return s.eval(x.Else, en)
	case *ir.Let:
		v, err := s.eval(x.Bound, en)
		if err != nil {
			return 0, err
		}
		child := &env{parent: en, vars: map[string]int64{}}
		if x.Name != "_" {
			child.vars[x.Name] = v
		}
		return s.eval(x.Body, child)
	case *ir.Call:
		return s.evalCall(x, en)
	default:
		return 0, diag.New(diag.CatInternal, fmt.Sprintf("unknown IR node %T", e))
	}
}

func (s *state) evalBinary(x *ir.Binary, en *env) (int64, error) {
	if x.Op == "&&" {
		l, err := s.eval(x.Lhs, en)
		if err != nil {
			return 0, err
		}
		if l == 0 {
			return 0, nil
		}
		r, err := s.eval(x.Rhs, en)
		if err != nil {
			return 0, err
		}
		return b2i(r != 0), nil
	}
	if x.Op == "||" {
		l, err := s.eval(x.Lhs, en)
		if err != nil {
			return 0, err
		}
		if l != 0 {
			return 1, nil
		}
		r, err := s.eval(x.Rhs, en)
		if err != nil {
			return 0, err
		}
		return b2i(r != 0), nil
	}
	l, err := s.eval(x.Lhs, en)
	if err != nil {
		return 0, err
	}
	r, err := s.eval(x.Rhs, en)
	if err != nil {
		return 0, err
	}
	return evalBinaryOp(x.Op, l, r)
}

func (s *state) evalCall(x *ir.Call, en *env) (int64, error) {
	if x.Name == ir.PrintName {
		v, err := s.eval(x.Args[0], en)
		if err != nil {
			return 0, err
		}
		s.trace.Outputs = append(s.trace.Outputs, v)
		return 0, nil
	}
	if x.Name == ir.ReadName {
		if s.trace.Reads >= len(s.inputs) {
			return 0, diag.New(diag.CatInput, fmt.Sprintf("read() called with no synthetic inputs left (reads=%d)", s.trace.Reads))
		}
		v := s.inputs[s.trace.Reads]
		s.trace.Reads++
		return v, nil
	}
	fn, ok := s.prog.Funcs[x.Name]
	if !ok {
		return 0, diag.New(diag.CatUnresolved, fmt.Sprintf("unknown function %q", x.Name))
	}
	if len(x.Args) != fn.Arity() {
		return 0, diag.New(diag.CatArity, fmt.Sprintf("%s expects %d args, got %d", x.Name, fn.Arity(), len(x.Args)))
	}
	if err := s.callEnter(); err != nil {
		return 0, err
	}
	defer s.callLeave()
	callee := &env{vars: map[string]int64{}}
	for i, p := range fn.Params {
		v, err := s.eval(x.Args[i], en)
		if err != nil {
			return 0, err
		}
		callee.vars[p.Name] = v
	}
	return s.eval(fn.Body, callee)
}

func b2i(b bool) int64 {
	if b {
		return 1
	}
	return 0
}

func evalUnary(op string, v int64) (int64, error) {
	switch op {
	case "-":
		return -v, nil
	case "!":
		return b2i(v == 0), nil
	default:
		return 0, diag.New(diag.CatSyntax, fmt.Sprintf("unknown unary op %q", op))
	}
}

func evalBinaryOp(op string, l, r int64) (int64, error) {
	switch op {
	case "+":
		return l + r, nil
	case "-":
		return l - r, nil
	case "*":
		return l * r, nil
	case "/":
		if r == 0 {
			return 0, diag.New(diag.CatDivisionByZero, "integer division by zero")
		}
		return l / r, nil
	case "%":
		if r == 0 {
			return 0, diag.New(diag.CatDivisionByZero, "integer modulo by zero")
		}
		return l % r, nil
	case "<":
		return b2i(l < r), nil
	case "<=":
		return b2i(l <= r), nil
	case ">":
		return b2i(l > r), nil
	case ">=":
		return b2i(l >= r), nil
	case "==":
		return b2i(l == r), nil
	case "!=":
		return b2i(l != r), nil
	default:
		return 0, diag.New(diag.CatSyntax, fmt.Sprintf("unknown binary op %q", op))
	}
}
