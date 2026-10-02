package specializer

import "funcspec/internal/ir"

// identity returns a semantically equivalent residual program without doing
// any specialization: every original function is copied with a fresh name
// prefix and call sites are rewritten. This is the global-budget fallback.
func identity(src *ir.Program, entry string, argv []Arg) *Result {
	prog := &ir.Program{Funcs: map[string]*ir.Func{}}
	idOf := func(name string) string { return "$id_" + name }
	for _, name := range src.Order {
		fn := src.Funcs[name]
		prog.Order = append(prog.Order, idOf(name))
		prog.Funcs[idOf(name)] = &ir.Func{
			Name:   idOf(name),
			Params: append([]string(nil), fn.Params...),
			Pure:   fn.Pure,
			Body:   cloneRewrite(fn.Body, idOf),
		}
	}
	resArgs := make([]ir.Expr, 0)
	fn := src.Funcs[entry]
	for i := range fn.Params {
		if !argv[i].Static {
			resArgs = append(resArgs, &ir.Var{Name: fn.Params[i]})
		}
	}
	// When all args are static but fallback occurred, the identity entry still
	// expects all parameters; supply literals for the static ones so the call
	// stays closed.
	if len(resArgs) == 0 || countDynamic(argv) != len(fn.Params) {
		resArgs = make([]ir.Expr, len(fn.Params))
		for i := range fn.Params {
			if argv[i].Static {
				resArgs[i] = &ir.Lit{V: argv[i].Value}
			} else {
				resArgs[i] = &ir.Var{Name: fn.Params[i]}
			}
		}
	}
	return &Result{
		Entry:    idOf(entry),
		Residual: prog,
		Args:     resArgs,
	}
}

func countDynamic(argv []Arg) int {
	n := 0
	for _, a := range argv {
		if !a.Static {
			n++
		}
	}
	return n
}

func cloneRewrite(stmts []ir.Stmt, idOf func(string) string) []ir.Stmt {
	var re func(e ir.Expr) ir.Expr
	var rs func(ss []ir.Stmt) []ir.Stmt
	rs = func(ss []ir.Stmt) []ir.Stmt {
		out := make([]ir.Stmt, len(ss))
		for i, s := range ss {
			switch x := s.(type) {
			case *ir.Let:
				out[i] = &ir.Let{Name: x.Name, Init: re(x.Init)}
			case *ir.Return:
				out[i] = &ir.Return{Value: re(x.Value)}
			case *ir.ExprStmt:
				out[i] = &ir.ExprStmt{X: re(x.X)}
			case *ir.If:
				out[i] = &ir.If{Cond: re(x.Cond), Then: rs(x.Then), Else: rs(x.Else), Pos: x.Pos}
			default:
				out[i] = s
			}
		}
		return out
	}
	re = func(e ir.Expr) ir.Expr {
		switch x := e.(type) {
		case *ir.Lit, *ir.Var:
			return e
		case *ir.Unary:
			return &ir.Unary{Op: x.Op, X: re(x.X)}
		case *ir.Binary:
			return &ir.Binary{Op: x.Op, X: re(x.X), Y: re(x.Y)}
		case *ir.Call:
			args := make([]ir.Expr, len(x.Args))
			for i, a := range x.Args {
				args[i] = re(a)
			}
			callee := x.Callee
			if callee != ir.EmitName {
				callee = idOf(callee)
			}
			return &ir.Call{Callee: callee, Args: args, Pos: x.Pos}
		}
		return e
	}
	return rs(stmts)
}
