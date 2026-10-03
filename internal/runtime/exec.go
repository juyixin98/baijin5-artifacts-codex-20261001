package runtime

import (
	"rlmod/internal/ir"
)

const callDepthLimit = 256

func (in *Interpreter) exec(start *frame) (Value, error) {
	frames := []*frame{start}
	depth := 0
	for len(frames) > 0 {
		fr := frames[len(frames)-1]
		fn := fr.fn
		advanced := false
		for fr.pc < len(fn.Instrs) {
			ins := fn.Instrs[fr.pc]
			fr.pc++
			switch ins.Op {
			case ir.OPushInt:
				fr.push(IntVal(ins.Int))
			case ir.OPushStr:
				fr.push(StrVal(ins.Str))
			case ir.OLoadLocal:
				if ins.Index >= len(fr.locals) {
					return Value{}, &RuntimeError{Category: ErrStack, Msg: "bad local index"}
				}
				fr.push(fr.locals[ins.Index])
			case ir.OStoreLocal:
				v, ok := fr.pop()
				if !ok {
					return Value{}, &RuntimeError{Category: ErrStack, Msg: "store with empty stack"}
				}
				if ins.Index >= len(fr.locals) {
					fr.locals = append(fr.locals, make([]Value, ins.Index+1-len(fr.locals))...)
				}
				fr.locals[ins.Index] = v
			case ir.ONeg:
				v, ok := fr.pop()
				if !ok || v.IsStr {
					return Value{}, &RuntimeError{Category: ErrType, Msg: "neg requires int"}
				}
				fr.push(IntVal(-v.Int))
			case ir.OBin:
				r, rok := fr.pop()
				l, lok := fr.pop()
				if !rok || !lok {
					return Value{}, &RuntimeError{Category: ErrStack, Msg: "binary underflow"}
				}
				res, err := binOp(ins.Str, l, r)
				if err != nil {
					return Value{}, err
				}
				fr.push(res)
			case ir.OJump:
				fr.pc = ins.Jump
			case ir.OJumpIfFalse:
				v, ok := fr.pop()
				if !ok {
					return Value{}, &RuntimeError{Category: ErrStack, Msg: "condition underflow"}
				}
				if v.IsStr || v.Int == 0 {
					fr.pc = ins.Jump
				}
			case ir.OPop:
				if _, ok := fr.pop(); !ok {
					return Value{}, &RuntimeError{Category: ErrStack, Msg: "pop underflow"}
				}
			case ir.OReturn:
				var ret Value
				if ins.Index == 1 {
					v, ok := fr.pop()
					if !ok {
						return Value{}, &RuntimeError{Category: ErrNoReturn, Msg: "return without value"}
					}
					ret = v
				}
				frames = frames[:len(frames)-1]
				if len(frames) == 0 {
					if fn.HasResult && ins.Index == 0 {
						return Value{}, &RuntimeError{Category: ErrNoReturn, Msg: fn.Key + " must return a value"}
					}
					return ret, nil
				}
				frames[len(frames)-1].push(ret)
				advanced = true
			case ir.OCall:
				callee := in.prog.Modules[ins.Module].Funcs[callKey(ins.Str, ins.Instance)]
				if callee == nil {
					return Value{}, &RuntimeError{Category: ErrUnknownFunc, Msg: "missing compiled callee " + ins.Module + "." + callKey(ins.Str, ins.Instance)}
				}
				if len(callee.Params) != ins.Index {
					return Value{}, &RuntimeError{Category: ErrArity, Msg: "stack arity mismatch"}
				}
				if depth >= callDepthLimit {
					return Value{}, &RuntimeError{Category: ErrStack, Msg: "call depth limit exceeded"}
				}
				nf := &frame{fn: callee, locals: make([]Value, len(callee.LocalTypes))}
				for i := ins.Index - 1; i >= 0; i-- {
					v, ok := fr.pop()
					if !ok {
						return Value{}, &RuntimeError{Category: ErrStack, Msg: "call args underflow"}
					}
					nf.locals[i] = v
				}
				frames = append(frames, nf)
				depth++
				advanced = true
			}
			if advanced {
				break
			}
		}
		if !advanced && fr.pc >= len(fr.fn.Instrs) {
			// fell off the end
			frames = frames[:len(frames)-1]
			if len(frames) == 0 {
				if fr.fn.HasResult {
					return Value{}, &RuntimeError{Category: ErrNoReturn, Msg: fr.fn.Key + " ended without return"}
				}
				return Value{}, nil
			}
		}
	}
	return Value{}, &RuntimeError{Category: ErrStack, Msg: "executor exhausted"}
}

func callKey(name, instance string) string {
	if instance == "" {
		return name
	}
	return name + "[" + instance + "]"
}

func binOp(op string, l, r Value) (Value, error) {
	switch op {
	case "+":
		if l.IsStr != r.IsStr {
			return Value{}, &RuntimeError{Category: ErrType, Msg: "+ operand types differ"}
		}
		if l.IsStr {
			return StrVal(l.Str + r.Str), nil
		}
		return IntVal(l.Int + r.Int), nil
	case "-":
		if l.IsStr || r.IsStr {
			return Value{}, &RuntimeError{Category: ErrType, Msg: "- on str"}
		}
		return IntVal(l.Int - r.Int), nil
	case "*":
		if l.IsStr || r.IsStr {
			return Value{}, &RuntimeError{Category: ErrType, Msg: "* on str"}
		}
		return IntVal(l.Int * r.Int), nil
	case "/":
		if l.IsStr || r.IsStr {
			return Value{}, &RuntimeError{Category: ErrType, Msg: "/ on str"}
		}
		if r.Int == 0 {
			return Value{}, &RuntimeError{Category: ErrDivZero, Msg: "division by zero"}
		}
		return IntVal(l.Int / r.Int), nil
	case "==":
		return IntVal(boolInt(valuesEqual(op, l, r))), nil
	case "!=":
		return IntVal(boolInt(!valuesEqual(op, l, r))), nil
	case "<", "<=", ">", ">=":
		if l.IsStr != r.IsStr {
			return Value{}, &RuntimeError{Category: ErrType, Msg: "compare types differ"}
		}
		var c int
		if l.IsStr {
			c = compareStr(l.Str, r.Str)
		} else {
			c = compareInt(l.Int, r.Int)
		}
		var ok bool
		switch op {
		case "<":
			ok = c < 0
		case "<=":
			ok = c <= 0
		case ">":
			ok = c > 0
		case ">=":
			ok = c >= 0
		}
		return IntVal(boolInt(ok)), nil
	}
	return Value{}, &RuntimeError{Category: ErrType, Msg: "unknown operator " + op}
}

func valuesEqual(op string, l, r Value) bool {
	if l.IsStr != r.IsStr {
		return false
	}
	if l.IsStr {
		return l.Str == r.Str
	}
	return l.Int == r.Int
}

func boolInt(b bool) int64 {
	if b {
		return 1
	}
	return 0
}

func compareInt(a, b int64) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	default:
		return 0
	}
}

func compareStr(a, b string) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	default:
		return 0
	}
}
