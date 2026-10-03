// Package interp executes the lowered stack-bytecode IR.
package interp

import (
	"fmt"

	"rlc/internal/ir"
)

type Error struct {
	Func   string
	Reason string
}

func (e *Error) Error() string { return "runtime error in " + e.Func + ": " + e.Reason }

type Engine struct {
	prog *ir.Program
}

func New(prog *ir.Program) *Engine { return &Engine{prog: prog} }

// Call runs a global function with integer/string/bool arguments.
func (e *Engine) Call(name string, args []ir.Value) (ir.Value, error) {
	f := e.prog.Func(name)
	if f == nil {
		return ir.Value{}, &Error{Func: name, Reason: "undefined function"}
	}
	if len(args) != len(f.Params) {
		return ir.Value{}, &Error{Func: name, Reason: fmt.Sprintf("arity %d != %d", len(args), len(f.Params))}
	}
	return e.run(f, args, 0)
}

type frame struct {
	f      *ir.Func
	locals []ir.Value
	ip     int
}

func (e *Engine) run(f *ir.Func, args []ir.Value, depth int) (ret ir.Value, err error) {
	if depth > 256 {
		return ir.Value{}, &Error{Func: f.Name, Reason: "call depth exceeded"}
	}
	fr := &frame{f: f, locals: make([]ir.Value, f.NumLocals)}
	copy(fr.locals, args)
	stack := make([]ir.Value, 0, 32)
	push := func(v ir.Value) { stack = append(stack, v) }
	pop := func() (ir.Value, bool) {
		if len(stack) == 0 {
			return ir.Value{}, false
		}
		v := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		return v, true
	}
	for fr.ip < len(f.Body) {
		in := f.Body[fr.ip]
		fr.ip++
		switch in.Op {
		case ir.OpConstInt:
			push(ir.Value{Type: "int", I: in.Int})
		case ir.OpConstStr:
			push(ir.Value{Type: "string", S: in.Str})
		case ir.OpConstBool:
			push(ir.Value{Type: "bool", B: in.Bool})
		case ir.OpLoad:
			push(fr.locals[in.Slot])
		case ir.OpStore:
			v, ok := pop()
			if !ok {
				return ir.Value{}, &Error{Func: f.Name, Reason: "empty stack on store"}
			}
			fr.locals[in.Slot] = v
		case ir.OpCall:
			cf := e.prog.Func(in.Str)
			if cf == nil {
				return ir.Value{}, &Error{Func: f.Name, Reason: "undefined callee " + in.Str}
			}
			cargs, err2 := popArgs(&stack, len(cf.Params))
			if err2 != nil {
				return ir.Value{}, &Error{Func: f.Name, Reason: err2.Error()}
			}
			rv, err2 := e.run(cf, cargs, depth+1)
			if err2 != nil {
				return ir.Value{}, err2
			}
			push(rv)
		case ir.OpGenericCall:
			cf := e.prog.Func(in.Str)
			if cf == nil {
				return ir.Value{}, &Error{Func: f.Name, Reason: "uninstantiated generic " + in.Str}
			}
			cargs, err2 := popArgs(&stack, len(cf.Params))
			if err2 != nil {
				return ir.Value{}, &Error{Func: f.Name, Reason: err2.Error()}
			}
			rv, err2 := e.run(cf, cargs, depth+1)
			if err2 != nil {
				return ir.Value{}, err2
			}
			push(rv)
		case ir.OpJump:
			fr.ip = in.Jump
		case ir.OpJumpIfFalse:
			v, ok := pop()
			if !ok || v.Type != "bool" {
				return ir.Value{}, &Error{Func: f.Name, Reason: "condition must be bool"}
			}
			if !v.B {
				fr.ip = in.Jump
			}
		case ir.OpReturn:
			v, ok := pop()
			if !ok {
				return ir.Value{}, &Error{Func: f.Name, Reason: "return with empty stack"}
			}
			return v, nil
		case ir.OpReturnVoid:
			return ir.Value{}, nil
		case ir.OpNeg:
			v, ok := pop()
			if !ok || v.Type != "int" {
				return ir.Value{}, &Error{Func: f.Name, Reason: "unary - on non-int"}
			}
			push(ir.Value{Type: "int", I: -v.I})
		case ir.OpBin:
			r, ok := pop()
			if !ok {
				return ir.Value{}, &Error{Func: f.Name, Reason: "bin missing rhs"}
			}
			l, ok := pop()
			if !ok {
				return ir.Value{}, &Error{Func: f.Name, Reason: "bin missing lhs"}
			}
			v, err2 := bin(in.Bin, l, r)
			if err2 != nil {
				return ir.Value{}, &Error{Func: f.Name, Reason: err2.Error()}
			}
			push(v)
		}
	}
	return ir.Value{}, nil
}

func popArgs(st *[]ir.Value, n int) ([]ir.Value, error) {
	if len(*st) < n {
		return nil, fmt.Errorf("call has %d stack values, need %d", len(*st), n)
	}
	args := make([]ir.Value, n)
	for i := n - 1; i >= 0; i-- {
		args[i] = (*st)[len(*st)-1]
		*st = (*st)[:len(*st)-1]
	}
	return args, nil
}

func bin(op ir.BinOp, l, r ir.Value) (ir.Value, error) {
	intBin := func(fn func(a, b int64) (int64, error)) (ir.Value, error) {
		if l.Type != "int" || r.Type != "int" {
			return ir.Value{}, fmt.Errorf("integer op on %s,%s", l.Type, r.Type)
		}
		v, err := fn(l.I, r.I)
		return ir.Value{Type: "int", I: v}, err
	}
	cmp := func(c bool) ir.Value { return ir.Value{Type: "bool", B: c} }
	switch op {
	case ir.BinAdd:
		if l.Type == "string" && r.Type == "string" {
			return ir.Value{Type: "string", S: l.S + r.S}, nil
		}
		return intBin(func(a, b int64) (int64, error) { return a + b, nil })
	case ir.BinSub:
		return intBin(func(a, b int64) (int64, error) { return a - b, nil })
	case ir.BinMul:
		return intBin(func(a, b int64) (int64, error) { return a * b, nil })
	case ir.BinDiv:
		return intBin(func(a, b int64) (int64, error) {
			if b == 0 {
				return 0, fmt.Errorf("division by zero")
			}
			return a / b, nil
		})
	case ir.BinMod:
		return intBin(func(a, b int64) (int64, error) {
			if b == 0 {
				return 0, fmt.Errorf("modulo by zero")
			}
			return a % b, nil
		})
	case ir.BinLt:
		if l.Type != r.Type {
			return ir.Value{}, fmt.Errorf("compare %s,%s", l.Type, r.Type)
		}
		switch l.Type {
		case "int":
			return cmp(l.I < r.I), nil
		case "string":
			return cmp(l.S < r.S), nil
		}
		return ir.Value{}, fmt.Errorf("cannot order %s", l.Type)
	case ir.BinLe:
		return cmpOrdered(l, r, func(cmpv int) bool { return cmpv <= 0 })
	case ir.BinGt:
		return cmpOrdered(l, r, func(cmpv int) bool { return cmpv > 0 })
	case ir.BinGe:
		return cmpOrdered(l, r, func(cmpv int) bool { return cmpv >= 0 })
	case ir.BinEq:
		return cmp(equal(l, r)), nil
	case ir.BinNe:
		return cmp(!equal(l, r)), nil
	}
	return ir.Value{}, fmt.Errorf("unknown binop %d", op)
}

func cmpOrdered(l, r ir.Value, f func(int) bool) (ir.Value, error) {
	if l.Type != r.Type {
		return ir.Value{}, fmt.Errorf("compare %s,%s", l.Type, r.Type)
	}
	c := 0
	switch l.Type {
	case "int":
		switch {
		case l.I < r.I:
			c = -1
		case l.I > r.I:
			c = 1
		}
	case "string":
		switch {
		case l.S < r.S:
			c = -1
		case l.S > r.S:
			c = 1
		}
	default:
		return ir.Value{}, fmt.Errorf("cannot order %s", l.Type)
	}
	return ir.Value{Type: "bool", B: f(c)}, nil
}

func equal(l, r ir.Value) bool {
	if l.Type != r.Type {
		return false
	}
	switch l.Type {
	case "int":
		return l.I == r.I
	case "string":
		return l.S == r.S
	case "bool":
		return l.B == r.B
	}
	return false
}
