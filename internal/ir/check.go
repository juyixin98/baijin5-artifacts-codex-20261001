package ir

import (
	"fmt"

	"funcspect/internal/diag"
)

// EntryName is the required entry function.
const EntryName = "main"

var builtinArity = map[string]int{PrintName: 1, ReadName: 0}

// Check validates names, arities, purity declarations and the entry function.
func Check(prog *Program) error {
	entry, ok := prog.Funcs[EntryName]
	if !ok {
		return diag.New(diag.CatSyntax, fmt.Sprintf("missing entry function %q", EntryName))
	}
	for i, p := range entry.Params {
		if !p.Static {
			return diag.New(diag.CatSyntax, fmt.Sprintf("entry %q parameter %q must be static; dynamic inputs come from %s()", EntryName, p.Name, ReadName)).At(entry.Line, entry.Col)
		}
		_ = i
	}
	// Purity analysis: a pure function may only call pure functions/operators.
	pure, err := analyzePurity(prog)
	if err != nil {
		return err
	}
	// Validate calls arities and names.
	for _, name := range prog.Order {
		f := prog.Funcs[name]
		var check func(Expr) error
		check = func(e Expr) error {
			switch x := e.(type) {
			case *Int, *Var:
				return nil
			case *Unary:
				return check(x.X)
			case *Binary:
				if err := check(x.Lhs); err != nil {
					return err
				}
				return check(x.Rhs)
			case *If:
				if err := check(x.Cond); err != nil {
					return err
				}
				if err := check(x.Then); err != nil {
					return err
				}
				return check(x.Else)
			case *Let:
				if err := check(x.Bound); err != nil {
					return err
				}
				return check(x.Body)
			case *Call:
				if arity, builtin := builtinArity[x.Name]; builtin {
					if len(x.Args) != arity {
						return diag.New(diag.CatArity, fmt.Sprintf("builtin %s expects %d args, got %d", x.Name, arity, len(x.Args)))
					}
				} else {
					callee, ok := prog.Funcs[x.Name]
					if !ok {
						return diag.New(diag.CatUnresolved, fmt.Sprintf("unknown function %q called in %q", x.Name, name))
					}
					if len(x.Args) != callee.Arity() {
						return diag.New(diag.CatArity, fmt.Sprintf("function %s expects %d args, got %d", x.Name, callee.Arity(), len(x.Args)))
					}
				}
				for _, a := range x.Args {
					if err := check(a); err != nil {
						return err
					}
				}
				return nil
			default:
				return diag.New(diag.CatInternal, fmt.Sprintf("unknown IR node %T", e))
			}
		}
		if err := check(f.Body); err != nil {
			return err
		}
	}
	_ = pure
	return nil
}

// analyzePurity computes the set of pure functions and rejects pure functions
// that transitively invoke effectful builtins or impure functions.
func analyzePurity(prog *Program) (map[string]bool, error) {
	state := map[string]int{} // 0 unknown, 1 in progress, 2 done
	pure := map[string]bool{}
	var visit func(name string) (bool, error)
	visit = func(name string) (bool, error) {
		f, ok := prog.Funcs[name]
		if !ok {
			return false, diag.New(diag.CatUnresolved, fmt.Sprintf("unknown function %q", name))
		}
		switch state[name] {
		case 1:
			// recursion: purity of the SCC still depends on bodies below.
			return f.Pure, nil
		case 2:
			return pure[name], nil
		}
		state[name] = 1
		bodyPure := true
		var walk func(Expr) error
		walk = func(e Expr) error {
			switch x := e.(type) {
			case *Int, *Var:
				return nil
			case *Unary:
				return walk(x.X)
			case *Binary:
				if err := walk(x.Lhs); err != nil {
					return err
				}
				return walk(x.Rhs)
			case *If:
				if err := walk(x.Cond); err != nil {
					return err
				}
				if err := walk(x.Then); err != nil {
					return err
				}
				return walk(x.Else)
			case *Let:
				if err := walk(x.Bound); err != nil {
					return err
				}
				return walk(x.Body)
			case *Call:
				if x.Name == PrintName || x.Name == ReadName {
					bodyPure = false
					return nil
				}
				callee, ok := prog.Funcs[x.Name]
				if !ok {
					return diag.New(diag.CatUnresolved, fmt.Sprintf("unknown function %q", x.Name))
				}
				cp, err := visit(x.Name)
				if err != nil {
					return err
				}
				if !callee.Pure || !cp {
					bodyPure = false
				}
				for _, a := range x.Args {
					if err := walk(a); err != nil {
						return err
					}
				}
				return nil
			default:
				return diag.New(diag.CatInternal, fmt.Sprintf("unknown IR node %T", e))
			}
		}
		if err := walk(f.Body); err != nil {
			return false, err
		}
		state[name] = 2
		pure[name] = bodyPure
		if f.Pure && !bodyPure {
			return false, diag.New(diag.CatImpureDecl, fmt.Sprintf("function %q is declared pure but performs side effects or calls impure functions", name)).At(f.Line, f.Col)
		}
		return bodyPure, nil
	}
	for _, name := range prog.Order {
		if _, err := visit(name); err != nil {
			return nil, err
		}
	}
	return pure, nil
}
