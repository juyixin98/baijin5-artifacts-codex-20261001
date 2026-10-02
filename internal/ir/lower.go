package ir

import (
	"funcspec/internal/errcat"
	"funcspec/internal/syntax"
)

// Lower resolves names, validates arity and purity and lowers an AST program
// into IR. Purity rule: a function declared pure must neither call emit nor
// call an impure user function; it may only perform pure arithmetic.
func Lower(prog *syntax.Program) (*Program, error) {
	out := &Program{Funcs: map[string]*Func{}}
	declared := map[string]*syntax.FuncDecl{}
	for _, fn := range prog.Funcs {
		if fn.Name == EmitName {
			return nil, errcat.New(errcat.Syntax, "cannot redefine builtin %q at %s", EmitName, fn.Pos)
		}
		if _, dup := declared[fn.Name]; dup {
			return nil, errcat.New(errcat.DuplicateDecl, "duplicate function %q at %s", fn.Name, fn.Pos)
		}
		for i := 0; i < len(fn.Params); i++ {
			for j := i + 1; j < len(fn.Params); j++ {
				if fn.Params[i] == fn.Params[j] {
					return nil, errcat.New(errcat.Syntax, "duplicate parameter %q in %q at %s", fn.Params[i], fn.Name, fn.Pos)
				}
			}
		}
		declared[fn.Name] = fn
		out.Order = append(out.Order, fn.Name)
	}

	if err := analyzePurity(declared, out.Order); err != nil {
		return nil, err
	}

	for _, name := range out.Order {
		fn := declared[name]
		body, err := lowerStmts(fn.Body, declared, fn)
		if err != nil {
			return nil, err
		}
		out.Funcs[name] = &Func{Name: name, Params: append([]string(nil), fn.Params...), Pure: fn.Pure, Body: body}
	}
	return out, nil
}

func lowerStmts(stmts []syntax.Stmt, declared map[string]*syntax.FuncDecl, fn *syntax.FuncDecl) ([]Stmt, error) {
	out := make([]Stmt, 0, len(stmts))
	for _, s := range stmts {
		lowered, err := lowerStmt(s, declared, fn)
		if err != nil {
			return nil, err
		}
		out = append(out, lowered...)
	}
	return out, nil
}

func lowerStmt(s syntax.Stmt, declared map[string]*syntax.FuncDecl, fn *syntax.FuncDecl) ([]Stmt, error) {
	switch st := s.(type) {
	case *syntax.LetStmt:
		e, err := lowerExpr(st.Init, declared, fn)
		if err != nil {
			return nil, err
		}
		return []Stmt{&Let{Name: st.Name, Init: e}}, nil
	case *syntax.ReturnStmt:
		if st.Value == nil {
			return []Stmt{&Return{Value: &Lit{V: Int(0)}}}, nil
		}
		e, err := lowerExpr(st.Value, declared, fn)
		if err != nil {
			return nil, err
		}
		return []Stmt{&Return{Value: e}}, nil
	case *syntax.IfStmt:
		cond, err := lowerExpr(st.Cond, declared, fn)
		if err != nil {
			return nil, err
		}
		thenB, err := lowerStmts(st.Then, declared, fn)
		if err != nil {
			return nil, err
		}
		var elseB []Stmt
		if st.Else != nil {
			elseB, err = lowerStmts(st.Else, declared, fn)
			if err != nil {
				return nil, err
			}
		}
		return []Stmt{&If{Cond: cond, Then: thenB, Else: elseB, Pos: st.Pos}}, nil
	case *syntax.ExprStmt:
		e, err := lowerExpr(st.X, declared, fn)
		if err != nil {
			return nil, err
		}
		return []Stmt{&ExprStmt{X: e}}, nil
	}
	return nil, errcat.New(errcat.Unknown, "unknown statement %T", s)
}

func lowerExpr(e syntax.Expr, declared map[string]*syntax.FuncDecl, fn *syntax.FuncDecl) (Expr, error) {
	switch ex := e.(type) {
	case *syntax.IntLit:
		return &Lit{V: Int(ex.Value)}, nil
	case *syntax.BoolLit:
		return &Lit{V: Bool(ex.Value)}, nil
	case *syntax.Ident:
		if !isVisible(ex.Name, fn) && !isBuiltinConst(ex.Name) {
			return nil, errcat.New(errcat.UndefinedSymbol, "undefined variable %q at %s (function %q)", ex.Name, ex.Pos, fn.Name)
		}
		return &Var{Name: ex.Name}, nil
	case *syntax.UnaryExpr:
		x, err := lowerExpr(ex.X, declared, fn)
		if err != nil {
			return nil, err
		}
		return &Unary{Op: ex.Op, X: x}, nil
	case *syntax.BinaryExpr:
		x, err := lowerExpr(ex.X, declared, fn)
		if err != nil {
			return nil, err
		}
		y, err := lowerExpr(ex.Y, declared, fn)
		if err != nil {
			return nil, err
		}
		return &Binary{Op: ex.Op, X: x, Y: y}, nil
	case *syntax.CallExpr:
		if ex.Callee != EmitName {
			callee, ok := declared[ex.Callee]
			if !ok {
				return nil, errcat.New(errcat.UndefinedSymbol, "undefined function %q at %s", ex.Callee, ex.Pos)
			}
			if len(ex.Args) != len(callee.Params) {
				return nil, errcat.New(errcat.Arity, "function %q expects %d args, got %d at %s", ex.Callee, len(callee.Params), len(ex.Args), ex.Pos)
			}
		} else {
			if len(ex.Args) != 1 {
				return nil, errcat.New(errcat.Arity, "emit expects 1 arg, got %d at %s", len(ex.Args), ex.Pos)
			}
		}
		args := make([]Expr, len(ex.Args))
		for i, a := range ex.Args {
			la, err := lowerExpr(a, declared, fn)
			if err != nil {
				return nil, err
			}
			args[i] = la
		}
		return &Call{Callee: ex.Callee, Args: args, Pos: ex.Pos}, nil
	}
	return nil, errcat.New(errcat.Unknown, "unknown expression %T", e)
}

func isVisible(name string, fn *syntax.FuncDecl) bool {
	for _, p := range fn.Params {
		if p == name {
			return true
		}
	}
	// Let bindings introduce names; lexical shadowing is allowed, so any
	// identifier that appears as a let target in the function is considered
	// visible. Full per-scope checks are enforced dynamically at runtime.
	return hasLetBinding(name, fn.Body)
}

func hasLetBinding(name string, stmts []syntax.Stmt) bool {
	for _, s := range stmts {
		switch st := s.(type) {
		case *syntax.LetStmt:
			if st.Name == name {
				return true
			}
			if exprBinds(st.Init, name) {
				return true
			}
		case *syntax.IfStmt:
			if hasLetBinding(name, st.Then) || (st.Else != nil && hasLetBinding(name, st.Else)) {
				return true
			}
		case *syntax.ReturnStmt:
			if st.Value != nil && exprBinds(st.Value, name) {
				return true
			}
		case *syntax.ExprStmt:
			if exprBinds(st.X, name) {
				return true
			}
		}
	}
	return false
}

func exprBinds(_ syntax.Expr, _ string) bool { return false }

func isBuiltinConst(name string) bool { return name == "true" || name == "false" }
