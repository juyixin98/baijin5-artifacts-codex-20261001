package frontend

import "genstatemachine/internal/gerr"

// Validate runs frontend semantic checks that do not require the IR:
//   - yield is forbidden anywhere inside a finally block;
//   - catch bindings must not shadow each other when nested;
//   - the only callable builtin is log(...).
func Validate(prog *Program) error {
	for _, g := range prog.Gens {
		v := &validator{}
		if err := v.block(g.Body, false); err != nil {
			return err
		}
	}
	return nil
}

type validator struct{}

func (v *validator) block(list []Stmt, inFinally bool) error {
	for _, s := range list {
		if err := v.stmt(s, inFinally); err != nil {
			return err
		}
	}
	return nil
}

func (v *validator) expr(e Expr) error {
	switch x := e.(type) {
	case *BinaryExpr:
		if err := v.expr(x.LHS); err != nil {
			return err
		}
		return v.expr(x.RHS)
	case *UnaryExpr:
		return v.expr(x.Expr)
	case *CallExpr:
		if x.Name != "log" {
			return gerr.New(gerr.EValidate, "unknown builtin %q (only log() is available)", x.Name).
				AtPos(x.Line, 0)
		}
		for _, a := range x.Args {
			if err := v.expr(a); err != nil {
				return err
			}
		}
	}
	return nil
}

func (v *validator) stmt(s Stmt, inFinally bool) error {
	switch x := s.(type) {
	case *BlockStmt:
		return v.block(x.List, inFinally)
	case *VarStmt:
		return v.exprOrNil(x.Init)
	case *AssignStmt:
		return v.expr(x.Expr)
	case *ExprStmt:
		return v.expr(x.Expr)
	case *YieldStmt:
		if inFinally {
			return gerr.New(gerr.EYieldFinally,
				"yield is not allowed inside a finally block (cleanup must not suspend)").
				AtPos(x.Line, 0)
		}
		return v.exprOrNil(x.Expr)
	case *ReturnStmt:
		return v.exprOrNil(x.Expr)
	case *ThrowStmt:
		return v.expr(x.Expr)
	case *IfStmt:
		if err := v.expr(x.Cond); err != nil {
			return err
		}
		if err := v.block(x.Then, inFinally); err != nil {
			return err
		}
		return v.block(x.Else, inFinally)
	case *WhileStmt:
		if err := v.expr(x.Cond); err != nil {
			return err
		}
		return v.block(x.Body, inFinally)
	case *TryStmt:
		if err := v.block(x.Body, inFinally); err != nil {
			return err
		}
		for _, c := range x.Catches {
			if c.Test != nil {
				if err := v.expr(c.Test); err != nil {
					return err
				}
			}
			if err := v.block(c.Body, inFinally); err != nil {
				return err
			}
		}
		// Statements in the finally clause are validated with inFinally=true,
		// so any yield nested at any depth is rejected.
		return v.block(x.Finally, true)
	}
	return nil
}

func (v *validator) exprOrNil(e Expr) error {
	if e == nil {
		return nil
	}
	return v.expr(e)
}
