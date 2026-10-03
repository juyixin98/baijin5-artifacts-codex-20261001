package runtime

import (
	"fmt"
	"strconv"

	"rlmod/internal/frontend"
	"rlmod/internal/ir"
)

// Value is a runtime value. Exactly one of Int/Str is meaningful.
type Value struct {
	IsStr bool
	Int   int64
	Str   string
}

func IntVal(v int64) Value  { return Value{Int: v} }
func StrVal(s string) Value { return Value{IsStr: true, Str: s} }

func (v Value) TypeName() string {
	if v.IsStr {
		return "str"
	}
	return "int"
}

func (v Value) Format() string {
	if v.IsStr {
		return v.Str
	}
	return strconv.FormatInt(v.Int, 10)
}

// RuntimeError is an execution failure with a classified category.
type RuntimeError struct {
	Category string
	Msg      string
}

func (e *RuntimeError) Error() string { return fmt.Sprintf("runtime[%s]: %s", e.Category, e.Msg) }

const (
	ErrNoEntry     = "no_entry"
	ErrArity       = "arity_mismatch"
	ErrUnknownFunc = "unknown_function"
	ErrType        = "type_error"
	ErrDivZero     = "division_by_zero"
	ErrNoReturn    = "missing_result"
	ErrStack       = "stack_underflow"
)

// Interpreter executes a linked ir.Program.
type Interpreter struct {
	prog *ir.Program
}

func New(prog *ir.Program) *Interpreter { return &Interpreter{prog: prog} }

// Entrypoint returns the function to run: an explicit Run/Main else the last
// module's last-declared exported result-bearing function.
func (in *Interpreter) Entrypoint() (module, key string, err error) {
	for _, want := range []string{"Run", "Main", "main"} {
		for _, m := range in.prog.Order {
			if _, ok := in.prog.Modules[m].Funcs[want]; ok {
				return m, want, nil
			}
		}
	}
	return "", "", &RuntimeError{Category: ErrNoEntry, Msg: "no Run/Main entrypoint found"}
}

// Run executes the zero-argument entrypoint and returns its result.
func (in *Interpreter) Run() (Value, error) {
	return in.CallWith("", "Run", nil)
}

// CallWith invokes module.fn (module "" searches all modules) with args.
func (in *Interpreter) CallWith(module, fn string, args []Value) (Value, error) {
	m, key := module, fn
	if module == "" {
		for _, mm := range in.prog.Order {
			if _, ok := in.prog.Modules[mm].Funcs[fn]; ok {
				m = mm
				break
			}
		}
		if m == "" {
			return Value{}, &RuntimeError{Category: ErrUnknownFunc, Msg: "function not found: " + fn}
		}
	}
	f := in.prog.Modules[m].Funcs[key]
	if f == nil {
		return Value{}, &RuntimeError{Category: ErrUnknownFunc, Msg: "function not found: " + m + "." + key}
	}
	if len(args) != len(f.Params) {
		return Value{}, &RuntimeError{Category: ErrArity, Msg: fmt.Sprintf("%s expects %d args, got %d", key, len(f.Params), len(args))}
	}
	frame := &frame{fn: f, locals: make([]Value, len(f.LocalTypes))}
	for i, a := range args {
		frame.locals[i] = a
	}
	return in.exec(frame)
}

type frame struct {
	pc     int
	fn     *ir.Func
	locals []Value
	stack  []Value
}

func (f *frame) push(v Value) { f.stack = append(f.stack, v) }
func (f *frame) pop() (Value, bool) {
	if len(f.stack) == 0 {
		return Value{}, false
	}
	v := f.stack[len(f.stack)-1]
	f.stack = f.stack[:len(f.stack)-1]
	return v, true
}

var _ = frontend.LitInt
