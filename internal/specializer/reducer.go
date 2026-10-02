package specializer

import (
	"funcspec/internal/errcat"
	"funcspec/internal/interp"
	"funcspec/internal/ir"
)

// reducer performs statement/expression reduction within one variant. The
// environment maps names known at compile time to constants; absent names are
// dynamic residual variables.
type reducer struct {
	st *state
	fn *ir.Func
	vr *variant
}

// static marks a reduced expression together with its compile-time value.
type sexpr struct {
	e      ir.Expr
	static bool
	v      ir.Value
}

func (r *reducer) reduceBlock(stmts []ir.Stmt, env map[string]ir.Value) ([]ir.Stmt, error) {
	out := []ir.Stmt{}
	for _, s := range stmts {
		switch st := s.(type) {
		case *ir.Let:
			rv, err := r.reduceExpr(st.Init, env)
			if err != nil {
				return nil, err
			}
			if rv.static {
				env[st.Name] = rv.v
			} else {
				out = append(out, &ir.Let{Name: st.Name, Init: rv.e})
			}
		case *ir.Return:
			rv, err := r.reduceExpr(st.Value, env)
			if err != nil {
				return nil, err
			}
			out = append(out, &ir.Return{Value: rv.e})
		case *ir.ExprStmt:
			rv, err := r.reduceExpr(st.X, env)
			if err != nil {
				return nil, err
			}
			// A fully static expression statement in a valid program can only
			// be a pure computation (emit is never static); drop it safely.
			if !rv.static {
				out = append(out, &ir.ExprStmt{X: rv.e})
			} else {
				r.st.log.Event("fold.drop", "static pure expression statement has no observable effect", nil)
			}
		case *ir.If:
			rv, err := r.reduceExpr(st.Cond, env)
			if err != nil {
				return nil, err
			}
			if rv.static {
				if rv.v.Kind != 'b' {
					// Match run-time semantics by residualizing; the
					// interpreter raises E_TYPE on execution.
					out = append(out, &ir.If{Cond: rv.e, Then: r.dup(st.Then), Else: r.dup(st.Else), Pos: st.Pos})
					continue
				}
				r.st.stat.BranchesPruned++
				chosen := st.Else
				which := "else"
				if rv.v.B {
					chosen = st.Then
					which = "then"
				}
				r.st.log.Event("branch.prune", "static condition; dead branch eliminated", map[string]any{
					"func": r.fn.Name, "cond": rv.v.B, "taken": which,
				})
				child := cloneEnv(env)
				more, err := r.reduceBlock(chosen, child)
				if err != nil {
					return nil, err
				}
				out = append(out, more...)
			} else {
				childT := cloneEnv(env)
				thenB, err := r.reduceBlock(st.Then, childT)
				if err != nil {
					return nil, err
				}
				var elseB []ir.Stmt
				if st.Else != nil {
					childE := cloneEnv(env)
					elseB, err = r.reduceBlock(st.Else, childE)
					if err != nil {
						return nil, err
					}
				}
				out = append(out, &ir.If{Cond: rv.e, Then: thenB, Else: elseB, Pos: st.Pos})
			}
		default:
			return nil, errcat.New(errcat.Unknown, "cannot reduce statement %T", s)
		}
	}
	return out, nil
}

func cloneEnv(env map[string]ir.Value) map[string]ir.Value {
	c := make(map[string]ir.Value, len(env))
	for k, v := range env {
		c[k] = v
	}
	return c
}

func (r *reducer) dup(stmts []ir.Stmt) []ir.Stmt {
	if stmts == nil {
		return nil
	}
	out := make([]ir.Stmt, len(stmts))
	copy(out, stmts)
	return out
}

func (r *reducer) reduceExpr(e ir.Expr, env map[string]ir.Value) (sexpr, error) {
	switch ex := e.(type) {
	case *ir.Lit:
		return sexpr{e: ex, static: true, v: ex.V}, nil
	case *ir.Var:
		if v, ok := env[ex.Name]; ok {
			return sexpr{e: &ir.Lit{V: v}, static: true, v: v}, nil
		}
		return sexpr{e: ex, static: false}, nil
	case *ir.Unary:
		x, err := r.reduceExpr(ex.X, env)
		if err != nil {
			return sexpr{}, err
		}
		if x.static {
			v, ferr := interp.EvalUnary(ex.Op, x.v)
			if ferr == nil {
				return sexpr{e: &ir.Lit{V: v}, static: true, v: v}, nil
			}
			// Preserve run-time failure: residualize and let execution raise.
			r.st.log.Event("fold.residualize", "static unary op would fail at runtime", map[string]any{
				"op": ex.Op, "reason": ferr.Error(),
			})
			return sexpr{e: &ir.Unary{Op: ex.Op, X: x.e}, static: false}, nil
		}
		return sexpr{e: &ir.Unary{Op: ex.Op, X: x.e}, static: false}, nil
	case *ir.Binary:
		x, err := r.reduceExpr(ex.X, env)
		if err != nil {
			return sexpr{}, err
		}
		y, err := r.reduceExpr(ex.Y, env)
		if err != nil {
			return sexpr{}, err
		}
		if x.static && y.static {
			v, ferr := interp.EvalPure(ex.Op, x.v, y.v)
			if ferr == nil {
				return sexpr{e: &ir.Lit{V: v}, static: true, v: v}, nil
			}
			r.st.log.Event("fold.residualize", "static binary op would fail (e.g. div by zero); keeping runtime semantics", map[string]any{
				"op": ex.Op, "reason": ferr.Error(),
			})
			return sexpr{e: &ir.Binary{Op: ex.Op, X: x.e, Y: y.e}, static: false}, nil
		}
		return sexpr{e: &ir.Binary{Op: ex.Op, X: x.e, Y: y.e}, static: false}, nil
	case *ir.Call:
		return r.reduceCall(ex, env)
	}
	return sexpr{}, errcat.New(errcat.Unknown, "cannot reduce expression %T", e)
}

func (r *reducer) reduceCall(c *ir.Call, env map[string]ir.Value) (sexpr, error) {
	args := make([]sexpr, len(c.Args))
	staticMask := make([]bool, len(c.Args))
	svals := make([]ir.Value, len(c.Args))
	for i, a := range c.Args {
		rv, err := r.reduceExpr(a, env)
		if err != nil {
			return sexpr{}, err
		}
		args[i] = rv
		staticMask[i] = rv.static
		svals[i] = rv.v
	}
	resArgs := make([]ir.Expr, len(args))
	for i, a := range args {
		resArgs[i] = a.e
	}

	// emit: the only impure builtin. Never folded, regardless of argument
	// knowledge, so effects cannot be performed ahead of time.
	if c.Callee == ir.EmitName {
		r.st.log.Event("effect.residualize", "emit call is impure; side effect kept at runtime", map[string]any{
			"func": r.fn.Name, "arg_static": staticMask[0],
		})
		return sexpr{e: &ir.Call{Callee: ir.EmitName, Args: resArgs, Pos: c.Pos}, static: false}, nil
	}

	callee := r.st.src.Funcs[c.Callee]
	if callee == nil {
		return sexpr{}, errcat.New(errcat.UndefinedSymbol, "undefined callee %q", c.Callee)
	}

	// Pure + every argument known: allowed to execute ahead of time, bounded by
	// the fold budget. Any inability to finish (budget, depth, would-be runtime
	// error) falls back to normal residual specialization.
	if callee.Pure && allStatic(staticMask) {
		if v, ferr := r.st.foldPure(c.Callee, svals); ferr == nil {
			r.st.stat.CallsFolded++
			r.st.log.Event("call.fold", "pure callee with all-known args executed ahead of time", map[string]any{
				"callee": c.Callee, "value": valueDetail(v),
			})
			return sexpr{e: &ir.Lit{V: v}, static: true, v: v}, nil
		} else {
			r.st.log.Event("call.fold.defer", "pure all-static call not folded; residualizing", map[string]any{
				"callee": c.Callee, "reason": ferr.Error(),
			})
		}
	}

	// Otherwise record a deferred specialized call. The target variant (and
	// the matching argument projection) is selected at assembly time, once the
	// final per-function cache and generalized variants are known.
	if _, err := r.st.requestVariant(c.Callee, staticMask, svals); err != nil {
		return sexpr{}, err
	}
	return sexpr{e: &ir.SpecializedCall{
		Src:    c.Callee,
		Args:   resArgs,
		Static: staticMask,
		SVal:   svals,
		Pos:    c.Pos,
	}, static: false}, nil
}
