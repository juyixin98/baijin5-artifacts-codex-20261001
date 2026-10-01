package ir

import (
	"fmt"

	"funcspect/internal/diag"
	"funcspect/internal/front"
)

// Build lowers an AST program to a validated IR program.
func Build(astProg *front.Program) (*Program, error) {
	prog := &Program{Funcs: map[string]*Func{}}
	for _, name := range astProg.Order {
		af := astProg.Funcs[name]
		f := &Func{Name: af.Name, Pure: af.Pure, Line: af.Line, Col: af.Col}
		seen := map[string]bool{}
		for _, ap := range af.Params {
			if seen[ap.Name] {
				return nil, diag.New(diag.CatSyntax, fmt.Sprintf("duplicate parameter %q in %q", ap.Name, name)).At(ap.Line, ap.Col)
			}
			seen[ap.Name] = true
			f.Params = append(f.Params, Param{Name: ap.Name, Static: ap.Static})
		}
		body, err := lowerExpr(af.Body)
		if err != nil {
			return nil, err
		}
		f.Body = body
		prog.Funcs[name] = f
		prog.Order = append(prog.Order, name)
	}
	if err := Check(prog); err != nil {
		return nil, err
	}
	return prog, nil
}

func lowerExpr(e front.Expr) (Expr, error) {
	switch x := e.(type) {
	case *front.IntLit:
		return &Int{Value: x.Value}, nil
	case *front.BoolLit:
		if x.Value {
			return &Int{Value: 1}, nil
		}
		return &Int{Value: 0}, nil
	case *front.VarRef:
		return &Var{Name: x.Name}, nil
	case *front.Unary:
		sub, err := lowerExpr(x.X)
		if err != nil {
			return nil, err
		}
		return &Unary{Op: x.Op, X: sub}, nil
	case *front.Binary:
		lhs, err := lowerExpr(x.Lhs)
		if err != nil {
			return nil, err
		}
		rhs, err := lowerExpr(x.Rhs)
		if err != nil {
			return nil, err
		}
		return &Binary{Op: x.Op, Lhs: lhs, Rhs: rhs}, nil
	case *front.IfExpr:
		cond, err := lowerExpr(x.Cond)
		if err != nil {
			return nil, err
		}
		thenE, err := lowerExpr(x.Then)
		if err != nil {
			return nil, err
		}
		elseE, err := lowerExpr(x.Else)
		if err != nil {
			return nil, err
		}
		return &If{Cond: cond, Then: thenE, Else: elseE}, nil
	case *front.LetExpr:
		bound, err := lowerExpr(x.Bound)
		if err != nil {
			return nil, err
		}
		body, err := lowerExpr(x.Body)
		if err != nil {
			return nil, err
		}
		return &Let{Name: x.Name, Bound: bound, Body: body}, nil
	case *front.CallExpr:
		call := &Call{Name: x.Name}
		for _, a := range x.Args {
			ae, err := lowerExpr(a)
			if err != nil {
				return nil, err
			}
			call.Args = append(call.Args, ae)
		}
		return call, nil
	default:
		return nil, diag.New(diag.CatInternal, fmt.Sprintf("unknown AST node %T", e))
	}
}
