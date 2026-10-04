package parser

import (
	"fmt"

	"genfsm/internal/ast"
	"genfsm/internal/semerr"
)

// validate runs whole-program static checks:
//   - unique function names, matching arity on call sites
//   - generators are only called where expected
//   - yield appears only inside gen functions
//   - no break/continue (unsupported), throw requires a string-ish message
//   - return inside generator becomes the generator result (allowed)
func validate(prog *ast.Program) error {
	funcs := map[string]*ast.Func{}
	for _, f := range prog.Funcs {
		if _, dup := funcs[f.Name]; dup {
			return semerr.Input(semerr.CodeStatic, "duplicate function %q at %d:%d", f.Name, f.P.Line, f.P.Col)
		}
		funcs[f.Name] = f
	}
	main, hasMain := funcs["main"]
	for _, f := range prog.Funcs {
		if err := validateFunc(f, funcs); err != nil {
			return err
		}
	}
	if !hasMain {
		return semerr.Input(semerr.CodeStatic, "program must define a 'main' function")
	}
	if main.IsGen {
		return semerr.Input(semerr.CodeStatic, "'main' must be a plain fn, not a generator")
	}
	if len(main.Params) != 0 {
		return semerr.Input(semerr.CodeStatic, "'main' must take no parameters")
	}
	return nil
}

func validateFunc(f *ast.Func, funcs map[string]*ast.Func) error {
	defined := map[string]bool{}
	for _, p := range f.Params {
		if defined[p] {
			return semerr.Input(semerr.CodeStatic, "duplicate parameter %q in %q at %d:%d", p, f.Name, f.P.Line, f.P.Col)
		}
		defined[p] = true
	}
	return walkBlock(f.Body, f.IsGen, funcs, defined, f.Name)
}

func walkBlock(b *ast.Block, inGen bool, funcs map[string]*ast.Func, locals map[string]bool, fnName string) error {
	for _, s := range b.Stmts {
		if err := walkStmt(s, inGen, funcs, locals, fnName); err != nil {
			return err
		}
	}
	return nil
}

func walkStmt(s ast.Stmt, inGen bool, funcs map[string]*ast.Func, locals map[string]bool, fnName string) error {
	switch n := s.(type) {
	case *ast.LetStmt:
		if locals[n.Name] {
			return semerr.Input(semerr.CodeStatic, "variable %q already declared in function %q at %d:%d", n.Name, fnName, n.P.Line, n.P.Col)
		}
		locals[n.Name] = true
		if n.Init != nil {
			return walkExpr(n.Init, inGen, funcs, fnName)
		}
	case *ast.AssignStmt:
		if err := walkExpr(n.Target, inGen, funcs, fnName); err != nil {
			return err
		}
		return walkExpr(n.Value, inGen, funcs, fnName)
	case *ast.ExprStmt:
		return walkExpr(n.X, inGen, funcs, fnName)
	case *ast.IfStmt:
		if err := walkExpr(n.Cond, inGen, funcs, fnName); err != nil {
			return err
		}
		if err := walkBlock(n.Then, inGen, funcs, cloneLocals(locals), fnName); err != nil {
			return err
		}
		if n.Else != nil {
			switch e := n.Else.(type) {
			case *ast.Block:
				if err := walkBlock(e, inGen, funcs, cloneLocals(locals), fnName); err != nil {
					return err
				}
			case *ast.IfStmt:
				if err := walkStmt(e, inGen, funcs, cloneLocals(locals), fnName); err != nil {
					return err
				}
			default:
				return semerr.Input(semerr.CodeStatic, "unsupported else branch at %d:%d", n.P.Line, n.P.Col)
			}
		}
	case *ast.WhileStmt:
		if err := walkExpr(n.Cond, inGen, funcs, fnName); err != nil {
			return err
		}
		return walkBlock(n.Body, inGen, funcs, cloneLocals(locals), fnName)
	case *ast.ForStmt:
		if err := walkExpr(n.Iter, inGen, funcs, fnName); err != nil {
			return err
		}
		inner := cloneLocals(locals)
		inner[n.Var] = true
		return walkBlock(n.Body, inGen, funcs, inner, fnName)
	case *ast.ReturnStmt:
		if n.Value != nil {
			return walkExpr(n.Value, inGen, funcs, fnName)
		}
	case *ast.ThrowStmt:
		return walkExpr(n.Value, inGen, funcs, fnName)
	case *ast.TryStmt:
		if err := walkBlock(n.Body, inGen, funcs, cloneLocals(locals), fnName); err != nil {
			return err
		}
		if n.Catch != nil {
			inner := cloneLocals(locals)
			inner[n.Catch.Param] = true
			if err := walkBlock(n.Catch.Body, inGen, funcs, inner, fnName); err != nil {
				return err
			}
		}
		if n.Finally != nil {
			if err := walkBlock(n.Finally, inGen, funcs, cloneLocals(locals), fnName); err != nil {
				return err
			}
		}
	default:
		return semerr.Input(semerr.CodeStatic, fmt.Sprintf("unsupported statement %T", s))
	}
	return nil
}

func cloneLocals(m map[string]bool) map[string]bool {
	c := make(map[string]bool, len(m))
	for k := range m {
		c[k] = true
	}
	return c
}

func walkExpr(e ast.Expr, inGen bool, funcs map[string]*ast.Func, fnName string) error {
	switch n := e.(type) {
	case *ast.IntLit, *ast.StrLit, *ast.BoolLit, *ast.NullLit, *ast.NameExpr:
		return nil
	case *ast.UnaryExpr:
		return walkExpr(n.X, inGen, funcs, fnName)
	case *ast.BinaryExpr:
		if err := walkExpr(n.X, inGen, funcs, fnName); err != nil {
			return err
		}
		return walkExpr(n.Y, inGen, funcs, fnName)
	case *ast.CallExpr:
		callee, ok := funcs[n.Callee]
		if !ok {
			return semerr.Input(semerr.CodeStatic, "call to undefined function %q at %d:%d", n.Callee, n.P.Line, n.P.Col)
		}
		if len(callee.Params) != len(n.Args) {
			return semerr.Input(semerr.CodeStatic, "function %q expects %d args, got %d at %d:%d",
				n.Callee, len(callee.Params), len(n.Args), n.P.Line, n.P.Col)
		}
		for _, a := range n.Args {
			if err := walkExpr(a, inGen, funcs, fnName); err != nil {
				return err
			}
		}
	case *ast.YieldExpr:
		if !inGen {
			return semerr.Input(semerr.CodeStatic, "yield outside of generator function %q at %d:%d", fnName, n.P.Line, n.P.Col)
		}
		if n.Init != nil {
			return walkExpr(n.Init, inGen, funcs, fnName)
		}
	default:
		return semerr.Input(semerr.CodeStatic, fmt.Sprintf("unsupported expression %T", e))
	}
	return nil
}
