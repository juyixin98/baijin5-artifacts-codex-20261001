// Package interp executes IR programs. The same evaluator runs both the
// original program and the residual program produced by the specializer, which
// is what makes the semantic differential a comparison of two real executions.
package interp

import (
	"funcspec/internal/errcat"
	"funcspec/internal/ir"
)

// Effect is one observable side effect produced by the emit builtin.
type Effect struct {
	Func string // function performing the emit
	Tag  int64  // emitted value
}

// Options controls interpreter resource limits.
type Options struct {
	MaxDepth int // maximum user-function call depth
	// OnCall, when set, is invoked for every user-function call performed.
	OnCall func(name string)
}

// DefaultOptions returns the standard resource limits.
func DefaultOptions() Options { return Options{MaxDepth: 200_000} }

// Result is the outcome of a top-level execution.
type Result struct {
	Value   int64
	Effects []Effect
}

type env struct {
	parent *env
	vars   map[string]ir.Value
}

func (e *env) lookup(name string) (ir.Value, bool) {
	for cur := e; cur != nil; cur = cur.parent {
		if v, ok := cur.vars[name]; ok {
			return v, true
		}
	}
	return ir.Value{}, false
}

type machine struct {
	prog    *ir.Program
	opts    Options
	effects []Effect
	curFunc string
	depth   int
}

// Run executes entry with integer/bool concrete arguments.
func Run(prog *ir.Program, entry string, args []ir.Value, opts Options) (*Result, error) {
	if _, ok := prog.Funcs[entry]; !ok {
		return nil, errcat.New(errcat.UndefinedSymbol, "no entry function %q", entry)
	}
	m := &machine{prog: prog, opts: opts}
	root := &env{vars: map[string]ir.Value{}}
	v, err := m.call(entry, args, root)
	if err != nil {
		return nil, err
	}
	if v.Kind != 'i' {
		return nil, errcat.New(errcat.Type, "entry %q returned non-integer value", entry)
	}
	return &Result{Value: v.I, Effects: m.effects}, nil
}

func (m *machine) call(name string, args []ir.Value, caller *env) (ir.Value, error) {
	if name != ir.EmitName {
		m.depth++
		if m.opts.OnCall != nil {
			m.opts.OnCall(name)
		}
		if m.depth > m.opts.MaxDepth {
			return ir.Value{}, errcat.New(errcat.StackDepth, "call depth exceeded %d in %q", m.opts.MaxDepth, name)
		}
		defer func() { m.depth-- }()
		fn := m.prog.Funcs[name]
		if fn == nil {
			return ir.Value{}, errcat.New(errcat.UndefinedSymbol, "undefined function %q", name)
		}
		if len(args) != len(fn.Params) {
			return ir.Value{}, errcat.New(errcat.Arity, "function %q expects %d args, got %d", name, len(fn.Params), len(args))
		}
		frame := &env{parent: caller, vars: map[string]ir.Value{}}
		for i, p := range fn.Params {
			frame.vars[p] = args[i]
		}
		prev := m.curFunc
		m.curFunc = name
		v, ret, err := m.execBlock(fn.Body, frame)
		m.curFunc = prev
		if err != nil {
			return ir.Value{}, err
		}
		if !ret {
			return ir.Value{}, errcat.New(errcat.NoReturn, "function %q ended without return", name)
		}
		return v, nil
	}
	// emit(x): record the effect and yield 0.
	if len(args) != 1 {
		return ir.Value{}, errcat.New(errcat.Arity, "emit expects 1 arg, got %d", len(args))
	}
	m.effects = append(m.effects, Effect{Func: m.curFunc, Tag: coerceInt(args[0])})
	return ir.Int(0), nil
}

// execBlock runs statements until a return (ret==true) or normal completion.
func (m *machine) execBlock(stmts []ir.Stmt, e *env) (val ir.Value, ret bool, err error) {
	for _, s := range stmts {
		switch st := s.(type) {
		case *ir.Let:
			v, err := m.eval(st.Init, e)
			if err != nil {
				return ir.Value{}, false, err
			}
			e.vars[st.Name] = v
		case *ir.Return:
			v, err := m.eval(st.Value, e)
			if err != nil {
				return ir.Value{}, false, err
			}
			return v, true, nil
		case *ir.If:
			c, err := m.eval(st.Cond, e)
			if err != nil {
				return ir.Value{}, false, err
			}
			if c.Kind != 'b' {
				return ir.Value{}, false, errcat.New(errcat.Type, "if condition is %s, want bool at %s", kindName(c.Kind), st.Pos)
			}
			branch := st.Else
			if c.B {
				branch = st.Then
			}
			if len(branch) > 0 {
				child := &env{parent: e, vars: map[string]ir.Value{}}
				v, r, err := m.execBlock(branch, child)
				if err != nil {
					return ir.Value{}, false, err
				}
				if r {
					return v, true, nil
				}
			}
		case *ir.ExprStmt:
			if _, err := m.eval(st.X, e); err != nil {
				return ir.Value{}, false, err
			}
		default:
			return ir.Value{}, false, errcat.New(errcat.Unknown, "unknown statement %T", s)
		}
	}
	return ir.Value{}, false, nil
}

func (m *machine) eval(e ir.Expr, env *env) (ir.Value, error) {
	switch ex := e.(type) {
	case *ir.Lit:
		return ex.V, nil
	case *ir.Var:
		v, ok := env.lookup(ex.Name)
		if !ok {
			return ir.Value{}, errcat.New(errcat.UndefinedSymbol, "undefined variable %q", ex.Name)
		}
		return v, nil
	case *ir.Unary:
		v, err := m.eval(ex.X, env)
		if err != nil {
			return ir.Value{}, err
		}
		return applyUnary(ex.Op, v)
	case *ir.Binary:
		x, err := m.eval(ex.X, env)
		if err != nil {
			return ir.Value{}, err
		}
		y, err := m.eval(ex.Y, env)
		if err != nil {
			return ir.Value{}, err
		}
		return applyBinary(ex.Op, x, y)
	case *ir.Call:
		args := make([]ir.Value, len(ex.Args))
		for i, a := range ex.Args {
			v, err := m.eval(a, env)
			if err != nil {
				return ir.Value{}, err
			}
			args[i] = v
		}
		return m.call(ex.Callee, args, env)
	}
	return ir.Value{}, errcat.New(errcat.Unknown, "unknown expression %T", e)
}

func kindName(k byte) string {
	if k == 'b' {
		return "bool"
	}
	return "int"
}
