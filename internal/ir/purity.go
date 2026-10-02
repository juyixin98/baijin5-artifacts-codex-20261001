package ir

import (
	"funcspec/internal/errcat"
	"funcspec/internal/syntax"
)

// analyzePurity computes, for every pure-annotated function, whether its body
// is actually free of the emit builtin and of calls to impure functions.
//
// It performs a three-color DFS over the call graph: a pure function is valid
// only when every reachable callee (transitively, including recursion cycles)
// is a pure user function. The first offending edge is reported with position.
func analyzePurity(declared map[string]*syntax.FuncDecl, order []string) error {
	// color: 0 white, 1 gray (on stack), 2 black (validated pure)
	color := map[string]int{}
	var visit func(name string, stack []string) error
	visit = func(name string, stack []string) error {
		if name == EmitName {
			return nil
		}
		switch color[name] {
		case 2:
			return nil
		case 1:
			// A recursion cycle among pure functions is fine: every edge was
			// purity-checked when it was first traversed.
			return nil
		}
		color[name] = 1
		fn := declared[name]
		var check func(e syntax.Expr) error
		checkStmts := func(stmts []syntax.Stmt) error { return nil }
		checkStmts = func(stmts []syntax.Stmt) error {
			for _, s := range stmts {
				switch st := s.(type) {
				case *syntax.LetStmt:
					if err := check(st.Init); err != nil {
						return err
					}
				case *syntax.ReturnStmt:
					if st.Value != nil {
						if err := check(st.Value); err != nil {
							return err
						}
					}
				case *syntax.IfStmt:
					if err := check(st.Cond); err != nil {
						return err
					}
					if err := checkStmts(st.Then); err != nil {
						return err
					}
					if st.Else != nil {
						if err := checkStmts(st.Else); err != nil {
							return err
						}
					}
				case *syntax.ExprStmt:
					if err := check(st.X); err != nil {
						return err
					}
				}
			}
			return nil
		}
		check = func(e syntax.Expr) error {
			switch ex := e.(type) {
			case *syntax.CallExpr:
				if ex.Callee == EmitName {
					return errcat.New(errcat.PurityViolation, "pure function %q calls impure builtin %q at %s", name, EmitName, ex.Pos)
				}
				callee, ok := declared[ex.Callee]
				if !ok {
					// undefined callee: reported during lowering expressions
					for _, a := range ex.Args {
						if err := check(a); err != nil {
							return err
						}
					}
					return nil
				}
				if !callee.Pure {
					return errcat.New(errcat.PurityViolation, "pure function %q calls impure function %q at %s", name, ex.Callee, ex.Pos)
				}
				if err := visit(ex.Callee, append(stack, name)); err != nil {
					return err
				}
				for _, a := range ex.Args {
					if err := check(a); err != nil {
						return err
					}
				}
			case *syntax.BinaryExpr:
				if err := check(ex.X); err != nil {
					return err
				}
				if err := check(ex.Y); err != nil {
					return err
				}
			case *syntax.UnaryExpr:
				if err := check(ex.X); err != nil {
					return err
				}
			}
			return nil
		}
		if err := checkStmts(fn.Body); err != nil {
			return err
		}
		color[name] = 2
		return nil
	}
	for _, name := range order {
		if declared[name].Pure {
			if err := visit(name, nil); err != nil {
				return err
			}
		}
	}
	return nil
}
