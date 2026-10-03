// Package ir defines the stack-bytecode intermediate representation produced
// by the lowering phase and consumed by the tree-walking interpreter.
package ir

type Op int

const (
	OpNop Op = iota
	OpConstInt
	OpConstStr
	OpConstBool
	OpLoad
	OpStore
	OpCall
	OpGenericCall // callee is a generic target; type arguments attached
	OpJump
	OpJumpIfFalse
	OpReturn
	OpReturnVoid
	OpNeg
	OpBin
)

type BinOp int

const (
	BinAdd BinOp = iota
	BinSub
	BinMul
	BinDiv
	BinMod
	BinLt
	BinLe
	BinGt
	BinGe
	BinEq
	BinNe
)

type Instr struct {
	Op    Op
	Int   int64
	Str   string
	Bool  bool
	Slot  int
	Bin   BinOp
	Jump  int
	Types []string
	Line  int
}

type Func struct {
	Name       string // global key: "module.name" or "module.name<type>"
	Module     string
	Short      string // unqualified name
	Params     []string
	ParamTypes []string
	Result     string
	Generic    bool
	Body       []Instr
	NumLocals  int
}

type Const struct {
	Name   string
	Module string
	Short  string
	Pub    bool
	Type   string
	Value  Value
}

// Value is the interpreter's tagged value.
type Value struct {
	Type string // "int" | "string" | "bool"
	I    int64
	S    string
	B    bool
}

type Program struct {
	Modules []string
	Funcs   []*Func
	Consts  []*Const
}

func (p *Program) Func(key string) *Func {
	for _, f := range p.Funcs {
		if f.Name == key {
			return f
		}
	}
	return nil
}

func (p *Program) Const_(key string) *Const {
	for _, c := range p.Consts {
		if c.Name == key {
			return c
		}
	}
	return nil
}
