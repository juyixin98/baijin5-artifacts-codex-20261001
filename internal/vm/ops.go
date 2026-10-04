package vm

import "genfsm/internal/value"

type exceptVal struct {
	name string
	msg  string
}

func (e exceptVal) Ok() bool { return e.name != "" }

func fail(name, msg string) exceptVal { return exceptVal{name: name, msg: msg} }

func evalUnary(op string, x value.Value) (value.Value, exceptVal) {
	switch op {
	case "-":
		if x.Tag != value.Int {
			return value.NullV(), fail("TypeError", "unary - requires int")
		}
		return value.IntV(-x.I), exceptVal{}
	case "+":
		if x.Tag != value.Int {
			return value.NullV(), fail("TypeError", "unary + requires int")
		}
		return x, exceptVal{}
	case "!":
		return value.BoolV(!x.IsTruthy()), exceptVal{}
	}
	return value.NullV(), fail("InternalError", "bad unary op")
}

func evalBinary(op string, x, y value.Value) (value.Value, exceptVal) {
	switch op {
	case "==":
		return value.BoolV(value.Equal(x, y)), exceptVal{}
	case "!=":
		return value.BoolV(!value.Equal(x, y)), exceptVal{}
	}
	// arithmetic / ordering on ints; string + and string ordering supported.
	if op == "+" && x.Tag == value.Str && y.Tag == value.Str {
		return value.StrV(x.S + y.S), exceptVal{}
	}
	if x.Tag == value.Int && y.Tag == value.Int {
		a, b := x.I, y.I
		switch op {
		case "+":
			return value.IntV(a + b), exceptVal{}
		case "-":
			return value.IntV(a - b), exceptVal{}
		case "*":
			return value.IntV(a * b), exceptVal{}
		case "/":
			if b == 0 {
				return value.NullV(), fail("DivByZero", "integer division by zero")
			}
			return value.IntV(a / b), exceptVal{}
		case "%":
			if b == 0 {
				return value.NullV(), fail("DivByZero", "integer modulo by zero")
			}
			return value.IntV(a % b), exceptVal{}
		case "<":
			return value.BoolV(a < b), exceptVal{}
		case "<=":
			return value.BoolV(a <= b), exceptVal{}
		case ">":
			return value.BoolV(a > b), exceptVal{}
		case ">=":
			return value.BoolV(a >= b), exceptVal{}
		}
	}
	if op == "<" || op == "<=" || op == ">" || op == ">=" {
		if x.Tag == value.Str && y.Tag == value.Str {
			switch op {
			case "<":
				return value.BoolV(x.S < y.S), exceptVal{}
			case "<=":
				return value.BoolV(x.S <= y.S), exceptVal{}
			case ">":
				return value.BoolV(x.S > y.S), exceptVal{}
			case ">=":
				return value.BoolV(x.S >= y.S), exceptVal{}
			}
		}
		return value.NullV(), fail("TypeError", "unsupported operands for <")
	}
	return value.NullV(), fail("TypeError", "unsupported operands for "+op)
}
