package frontend

// validate performs semantic checks after parsing:
//   - constructor patterns reference declared constructors with matching arity
//   - variables are not bound twice in one pattern (non-linear patterns are
//     rejected rather than silently re-bound)
//   - guard variables are bound by the branch's own pattern (scope check)
//   - guard functions are known builtins with matching arity
func validate(prog *Program) *Error {
	for i := range prog.Branches {
		b := &prog.Branches[i]
		vars := map[string]bool{}
		if err := checkPattern(b.Pat, prog.Ctors, vars); err != nil {
			return err
		}
		if b.Guard != nil {
			if err := checkExpr(b.Guard, vars); err != nil {
				return err
			}
		}
	}
	return nil
}

func checkPattern(p Pattern, ctors map[string]int, vars map[string]bool) *Error {
	switch t := p.(type) {
	case PWildcard:
		return nil
	case PVar:
		if vars[t.Name] {
			return errorf(CatSemantic, t.P, "duplicate variable %q in pattern", t.Name)
		}
		vars[t.Name] = true
		return nil
	case PLit:
		return nil
	case PCtor:
		arity, ok := ctors[t.Name]
		if !ok {
			return errorf(CatSemantic, t.P, "unknown constructor %q", t.Name)
		}
		if len(t.Args) != arity {
			return errorf(CatSemantic, t.P, "constructor %q expects %d argument(s), got %d", t.Name, arity, len(t.Args))
		}
		for _, a := range t.Args {
			if err := checkPattern(a, ctors, vars); err != nil {
				return err
			}
		}
		return nil
	}
	return nil
}

func checkExpr(e Expr, vars map[string]bool) *Error {
	switch t := e.(type) {
	case ELit:
		return nil
	case EVar:
		if !vars[t.Name] {
			return errorf(CatSemantic, t.P, "unbound variable %q in guard", t.Name)
		}
		return nil
	case ECall:
		arity, ok := Builtins[t.Func]
		if !ok {
			return errorf(CatSemantic, t.P, "unknown guard function %q", t.Func)
		}
		if len(t.Args) != arity {
			return errorf(CatSemantic, t.P, "function %q expects %d argument(s), got %d", t.Func, arity, len(t.Args))
		}
		for _, a := range t.Args {
			if err := checkExpr(a, vars); err != nil {
				return err
			}
		}
		return nil
	case EAnd:
		if err := checkExpr(t.L, vars); err != nil {
			return err
		}
		return checkExpr(t.R, vars)
	case EOr:
		if err := checkExpr(t.L, vars); err != nil {
			return err
		}
		return checkExpr(t.R, vars)
	case ENot:
		return checkExpr(t.E, vars)
	}
	return nil
}
