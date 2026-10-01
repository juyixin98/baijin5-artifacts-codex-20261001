package runtime

import (
	"genstatemachine/internal/gerr"
)

// evalUnary applies ! and unary -.
func evalUnary(op string, v Value) (Value, error) {
	switch op {
	case "!":
		return BoolVal(!v.Truthy()), nil
	case "-":
		if v.Kind != VInt {
			return Value{}, gerr.New(gerr.CTypeMismatch, "unary '-' requires int, got %s", v.Display())
		}
		return IntVal(-v.I), nil
	}
	return Value{}, gerr.New(gerr.CTypeMismatch, "unknown unary operator %q", op)
}

// evalBinary applies arithmetic, comparison and logical operators.
//
// Note: && and || are compiled eagerly (no short-circuit) by the frontend CFG;
// operands are side-effect-free except log, so eager evaluation matches the
// observable result for the supported language.
func evalBinary(op string, l, r Value) (Value, error) {
	switch op {
	case "&&":
		return BoolVal(l.Truthy() && r.Truthy()), nil
	case "||":
		return BoolVal(l.Truthy() || r.Truthy()), nil
	case "==":
		return BoolVal(l.Equals(r)), nil
	case "!=":
		return BoolVal(!l.Equals(r)), nil
	}

	switch op {
	case "+", "-", "*", "/", "%":
		if op == "+" && l.Kind == VStr && r.Kind == VStr {
			return StrVal(l.S + r.S), nil
		}
		if l.Kind != VInt || r.Kind != VInt {
			return Value{}, gerr.New(gerr.CTypeMismatch,
				"operator %q requires two ints, got %s and %s", op, l.Display(), r.Display())
		}
		switch op {
		case "+":
			return IntVal(l.I + r.I), nil
		case "-":
			return IntVal(l.I - r.I), nil
		case "*":
			return IntVal(l.I * r.I), nil
		case "/":
			if r.I == 0 {
				return Value{}, gerr.New(gerr.CDivZero, "integer division by zero")
			}
			return IntVal(l.I / r.I), nil
		case "%":
			if r.I == 0 {
				return Value{}, gerr.New(gerr.CDivZero, "integer modulo by zero")
			}
			return IntVal(l.I % r.I), nil
		}
	case "<", "<=", ">", ">=":
		if l.Kind != VInt || r.Kind != VInt {
			return Value{}, gerr.New(gerr.CTypeMismatch,
				"operator %q requires two ints, got %s and %s", op, l.Display(), r.Display())
		}
		var b bool
		switch op {
		case "<":
			b = l.I < r.I
		case "<=":
			b = l.I <= r.I
		case ">":
			b = l.I > r.I
		case ">=":
			b = l.I >= r.I
		}
		return BoolVal(b), nil
	}
	return Value{}, gerr.New(gerr.CTypeMismatch, "unknown binary operator %q", op)
}
