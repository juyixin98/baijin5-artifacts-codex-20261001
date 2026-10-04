package interp

import (
	"genfsm/internal/ast"
	"genfsm/internal/value"
)

func (in *Interpreter) eval(e ast.Expr, env *scope) (value.Value, excVal) {
	switch n := e.(type) {
	case *ast.NullLit:
		return value.NullV(), excVal{}
	case *ast.BoolLit:
		return value.BoolV(n.Value), excVal{}
	case *ast.IntLit:
		return value.IntV(n.Value), excVal{}
	case *ast.StrLit:
		return value.StrV(n.Value), excVal{}
	case *ast.NameExpr:
		if p, ok := env.lookup(n.Name); ok {
			return *p, excVal{}
		}
		return value.NullV(), exf("NameError", "undefined variable "+n.Name)
	case *ast.UnaryExpr:
		x, ex := in.eval(n.X, env)
		if ex.Ok() {
			return value.NullV(), ex
		}
		return evalUnary(n.Op, x)
	case *ast.BinaryExpr:
		if n.Op == "&&" {
			x, ex := in.eval(n.X, env)
			if ex.Ok() {
				return value.NullV(), ex
			}
			if !x.IsTruthy() {
				return x, excVal{}
			}
			return in.eval(n.Y, env)
		}
		if n.Op == "||" {
			x, ex := in.eval(n.X, env)
			if ex.Ok() {
				return value.NullV(), ex
			}
			if x.IsTruthy() {
				return x, excVal{}
			}
			return in.eval(n.Y, env)
		}
		x, ex := in.eval(n.X, env)
		if ex.Ok() {
			return value.NullV(), ex
		}
		y, ex := in.eval(n.Y, env)
		if ex.Ok() {
			return value.NullV(), ex
		}
		return evalBinary(n.Op, x, y)
	case *ast.CallExpr:
		return in.evalCall(n, env)
	case *ast.YieldExpr:
		// Yield is only valid inside a generator goroutine; the concrete
		// suspension is implemented in gen.go via a thread-local hook.
		v := value.NullV()
		if n.Init != nil {
			var ex excVal
			v, ex = in.eval(n.Init, env)
			if ex.Ok() {
				return value.NullV(), ex
			}
		}
		yfn := env.yieldFn()
		if yfn == nil {
			return value.NullV(), exf("InternalError", "yield outside generator")
		}
		rv, _ := yfn(v)
		if rv.Tag == value.Exception {
			return value.NullV(), excVal{name: rv.ExName, msg: rv.ExMessage}
		}
		return rv, excVal{}
	}
	return value.NullV(), exf("InternalError", "bad expression")
}

func evalUnary(op string, x value.Value) (value.Value, excVal) {
	switch op {
	case "-":
		if x.Tag != value.Int {
			return value.NullV(), exf("TypeError", "unary - requires int")
		}
		return value.IntV(-x.I), excVal{}
	case "+":
		if x.Tag != value.Int {
			return value.NullV(), exf("TypeError", "unary + requires int")
		}
		return x, excVal{}
	case "!":
		return value.BoolV(!x.IsTruthy()), excVal{}
	}
	return value.NullV(), exf("InternalError", "bad unary op")
}

func evalBinary(op string, x, y value.Value) (value.Value, excVal) {
	if op == "+" && x.Tag == value.Str && y.Tag == value.Str {
		return value.StrV(x.S + y.S), excVal{}
	}
	if op == "==" {
		return value.BoolV(value.Equal(x, y)), excVal{}
	}
	if op == "!=" {
		return value.BoolV(!value.Equal(x, y)), excVal{}
	}
	if x.Tag == value.Int && y.Tag == value.Int {
		a, b := x.I, y.I
		switch op {
		case "+":
			return value.IntV(a + b), excVal{}
		case "-":
			return value.IntV(a - b), excVal{}
		case "*":
			return value.IntV(a * b), excVal{}
		case "/":
			if b == 0 {
				return value.NullV(), exf("DivByZero", "integer division by zero")
			}
			return value.IntV(a / b), excVal{}
		case "%":
			if b == 0 {
				return value.NullV(), exf("DivByZero", "integer modulo by zero")
			}
			return value.IntV(a % b), excVal{}
		case "<":
			return value.BoolV(a < b), excVal{}
		case "<=":
			return value.BoolV(a <= b), excVal{}
		case ">":
			return value.BoolV(a > b), excVal{}
		case ">=":
			return value.BoolV(a >= b), excVal{}
		}
	}
	if (op == "<" || op == "<=" || op == ">" || op == ">=") &&
		x.Tag == value.Str && y.Tag == value.Str {
		switch op {
		case "<":
			return value.BoolV(x.S < y.S), excVal{}
		case "<=":
			return value.BoolV(x.S <= y.S), excVal{}
		case ">":
			return value.BoolV(x.S > y.S), excVal{}
		case ">=":
			return value.BoolV(x.S >= y.S), excVal{}
		}
	}
	return value.NullV(), exf("TypeError", "unsupported operands for "+op)
}

func (in *Interpreter) evalCall(n *ast.CallExpr, env *scope) (value.Value, excVal) {
	fn, ok := in.funcs[n.Callee]
	if !ok {
		return value.NullV(), exf("NameError", "unknown function "+n.Callee)
	}
	args := make([]value.Value, len(n.Args))
	for i, a := range n.Args {
		v, ex := in.eval(a, env)
		if ex.Ok() {
			return value.NullV(), ex
		}
		args[i] = v
	}
	if fn.IsGen {
		g := in.spawnGen(fn, args)
		return value.GenV(g), excVal{}
	}
	return in.callPlain(fn, args)
}

func (in *Interpreter) callPlain(fn *ast.Func, args []value.Value) (value.Value, excVal) {
	env := newScope(nil)
	for i, p := range fn.Params {
		var av value.Value = value.NullV()
		if i < len(args) {
			av = args[i]
		}
		env.define(p, av)
	}
	return in.execFuncBody(fn.Body, env)
}
