package runtime

import (
	"fmt"

	"pmd/frontend"
)

// Lookup resolves a guard variable to its bound value.
type Lookup func(name string) (*Value, bool)

// GuardError is a runtime guard evaluation error (category guard_error).
type GuardError struct{ Msg string }

func (e *GuardError) Error() string { return e.Msg }

func guardErrorf(format string, args ...any) *GuardError {
	return &GuardError{Msg: fmt.Sprintf(format, args...)}
}

// EvalGuard evaluates a guard expression, appending side effects to
// effects. "and"/"or" short-circuit, so their effects are only recorded
// for operands actually evaluated.
func EvalGuard(e frontend.Expr, lk Lookup, effects *[]Effect) (frontend.LitValue, error) {
	switch t := e.(type) {
	case frontend.ELit:
		return t.Val, nil
	case frontend.EVar:
		v, ok := lk(t.Name)
		if !ok {
			return frontend.LitValue{}, guardErrorf("unbound variable %q", t.Name)
		}
		if v.Kind != VLit {
			return frontend.LitValue{}, guardErrorf("variable %q is bound to %s, not a literal", t.Name, v)
		}
		return v.Lit, nil
	case frontend.EAnd:
		l, err := EvalGuard(t.L, lk, effects)
		if err != nil {
			return frontend.LitValue{}, err
		}
		if err := needBool("and", l); err != nil {
			return frontend.LitValue{}, err
		}
		if !l.Bool {
			return frontend.BoolLit(false), nil
		}
		r, err := EvalGuard(t.R, lk, effects)
		if err != nil {
			return frontend.LitValue{}, err
		}
		if err := needBool("and", r); err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(r.Bool), nil
	case frontend.EOr:
		l, err := EvalGuard(t.L, lk, effects)
		if err != nil {
			return frontend.LitValue{}, err
		}
		if err := needBool("or", l); err != nil {
			return frontend.LitValue{}, err
		}
		if l.Bool {
			return frontend.BoolLit(true), nil
		}
		r, err := EvalGuard(t.R, lk, effects)
		if err != nil {
			return frontend.LitValue{}, err
		}
		if err := needBool("or", r); err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(r.Bool), nil
	case frontend.ENot:
		v, err := EvalGuard(t.E, lk, effects)
		if err != nil {
			return frontend.LitValue{}, err
		}
		if err := needBool("not", v); err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(!v.Bool), nil
	case frontend.ECall:
		return callBuiltin(t, lk, effects)
	}
	return frontend.LitValue{}, guardErrorf("unknown expression %T", e)
}

func needBool(where string, v frontend.LitValue) error {
	if v.Kind != frontend.LitBool {
		return guardErrorf("%s: expected boolean operand, got %s", where, v)
	}
	return nil
}

func callBuiltin(c frontend.ECall, lk Lookup, effects *[]Effect) (frontend.LitValue, error) {
	args := make([]frontend.LitValue, len(c.Args))
	for i, a := range c.Args {
		v, err := EvalGuard(a, lk, effects)
		if err != nil {
			return frontend.LitValue{}, err
		}
		args[i] = v
	}
	switch c.Func {
	case "eq":
		return frontend.BoolLit(args[0] == args[1]), nil
	case "ne":
		return frontend.BoolLit(args[0] != args[1]), nil
	case "lt", "le", "gt", "ge":
		return compare(c.Func, args[0], args[1])
	case "even":
		n, err := needInt(c.Func, args[0])
		if err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(n%2 == 0), nil
	case "odd":
		n, err := needInt(c.Func, args[0])
		if err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(n%2 != 0), nil
	case "pos":
		n, err := needInt(c.Func, args[0])
		if err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(n > 0), nil
	case "neg":
		n, err := needInt(c.Func, args[0])
		if err != nil {
			return frontend.LitValue{}, err
		}
		return frontend.BoolLit(n < 0), nil
	case "effect":
		if args[0].Kind != frontend.LitStr {
			return frontend.LitValue{}, guardErrorf("effect: label must be a string, got %s", args[0])
		}
		if err := needBool("effect", args[1]); err != nil {
			return frontend.LitValue{}, err
		}
		*effects = append(*effects, Effect{Label: args[0].Str, Value: args[1]})
		return args[1], nil
	}
	return frontend.LitValue{}, guardErrorf("unknown guard function %q", c.Func)
}

func needInt(fn string, v frontend.LitValue) (int64, error) {
	if v.Kind != frontend.LitInt {
		return 0, guardErrorf("%s: expected integer operand, got %s", fn, v)
	}
	return v.Int, nil
}

func compare(fn string, a, b frontend.LitValue) (frontend.LitValue, error) {
	if a.Kind != b.Kind {
		return frontend.LitValue{}, guardErrorf("%s: cannot compare %s with %s", fn, a.Kind, b.Kind)
	}
	var cmp int
	switch a.Kind {
	case frontend.LitInt:
		cmp = compareOrdered(a.Int, b.Int)
	case frontend.LitStr:
		cmp = compareOrdered(a.Str, b.Str)
	default:
		return frontend.LitValue{}, guardErrorf("%s: cannot order %s operands", fn, a.Kind)
	}
	var r bool
	switch fn {
	case "lt":
		r = cmp < 0
	case "le":
		r = cmp <= 0
	case "gt":
		r = cmp > 0
	case "ge":
		r = cmp >= 0
	}
	return frontend.BoolLit(r), nil
}

func compareOrdered[T int64 | string](a, b T) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	}
	return 0
}
