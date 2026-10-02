package interp

import (
	"funcspec/internal/errcat"
	"funcspec/internal/ir"
)

// EvalPure is the shared implementation of every pure primitive. It is used by
// the interpreter at runtime and by the specializer during static reduction, so
// compile-time and run-time arithmetic cannot drift apart.
func EvalPure(op string, x, y ir.Value) (ir.Value, error) {
	return applyBinary(op, x, y)
}

// EvalUnary applies a pure prefix operator.
func EvalUnary(op string, x ir.Value) (ir.Value, error) {
	return applyUnary(op, x)
}

func applyUnary(op string, x ir.Value) (ir.Value, error) {
	switch op {
	case "-":
		if x.Kind != 'i' {
			return ir.Value{}, errcat.New(errcat.Type, "unary '-' needs int, got %s", kindName(x.Kind))
		}
		return ir.Int(-x.I), nil
	case "!":
		if x.Kind != 'b' {
			return ir.Value{}, errcat.New(errcat.Type, "unary '!' needs bool, got %s", kindName(x.Kind))
		}
		return ir.Bool(!x.B), nil
	}
	return ir.Value{}, errcat.New(errcat.Unknown, "unknown unary op %q", op)
}

func applyBinary(op string, x, y ir.Value) (ir.Value, error) {
	switch op {
	case "&&", "||":
		if x.Kind != 'b' || y.Kind != 'b' {
			return ir.Value{}, errcat.New(errcat.Type, "operator %q needs bool operands, got %s,%s", op, kindName(x.Kind), kindName(y.Kind))
		}
		if op == "&&" {
			return ir.Bool(x.B && y.B), nil
		}
		return ir.Bool(x.B || y.B), nil
	case "==", "!=":
		if x.Kind != y.Kind {
			return ir.Value{}, errcat.New(errcat.Type, "operator %q mixes %s and %s", op, kindName(x.Kind), kindName(y.Kind))
		}
		eq := (x.Kind == 'i' && x.I == y.I) || (x.Kind == 'b' && x.B == y.B)
		if op == "!=" {
			eq = !eq
		}
		return ir.Bool(eq), nil
	case "<", ">", "<=", ">=":
		if x.Kind != 'i' || y.Kind != 'i' {
			return ir.Value{}, errcat.New(errcat.Type, "operator %q needs int operands, got %s,%s", op, kindName(x.Kind), kindName(y.Kind))
		}
		var r bool
		switch op {
		case "<":
			r = x.I < y.I
		case ">":
			r = x.I > y.I
		case "<=":
			r = x.I <= y.I
		case ">=":
			r = x.I >= y.I
		}
		return ir.Bool(r), nil
	case "+", "-", "*", "/", "%":
		if x.Kind != 'i' || y.Kind != 'i' {
			return ir.Value{}, errcat.New(errcat.Type, "operator %q needs int operands, got %s,%s", op, kindName(x.Kind), kindName(y.Kind))
		}
		switch op {
		case "+":
			return ir.Int(x.I + y.I), nil
		case "-":
			return ir.Int(x.I - y.I), nil
		case "*":
			return ir.Int(x.I * y.I), nil
		case "/":
			if y.I == 0 {
				return ir.Value{}, errcat.New(errcat.DivisionByZero, "division by zero")
			}
			return ir.Int(x.I / y.I), nil
		case "%":
			if y.I == 0 {
				return ir.Value{}, errcat.New(errcat.DivisionByZero, "modulo by zero")
			}
			return ir.Int(x.I % y.I), nil
		}
	}
	return ir.Value{}, errcat.New(errcat.Unknown, "unknown binary op %q", op)
}

func coerceInt(v ir.Value) int64 {
	if v.Kind == 'b' {
		if v.B {
			return 1
		}
		return 0
	}
	return v.I
}
