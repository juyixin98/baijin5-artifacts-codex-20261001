// Package ir defines the restricted masked-SIMD IR produced by lowering.
//
// Every memory access and every potentially trapping arithmetic operation
// (division/modulo) carries an explicit predicate mask register. Mask values
// are produced only by VMASKTAIL / VCMP / VAND / VNOT, so the structural
// validator can prove that masks are derived from the explicit lane-valid
// tail mask.
package ir

// Opcode enumerates restricted vector instructions.
type Opcode string

const (
	OpMaskTail Opcode = "VMASKTAIL" // R_d = lanes with (Base+lane) < Count
	OpConst    Opcode = "VCONST"    // broadcast constant
	OpIVar     Opcode = "VIVAR"     // broadcast loop index Base+lane per lane
	OpLoadS    Opcode = "VLOADSCALAR"
	OpLoad     Opcode = "VLOAD" // masked gather from array
	OpBin      Opcode = "VBIN"  // masked binary arithmetic
	OpCmp      Opcode = "VCMP"  // masked compare -> mask
	OpAnd      Opcode = "VAND"  // mask AND
	OpNot      Opcode = "VNOT"  // mask NOT
	OpStore    Opcode = "VSTORE"
	OpReduce   Opcode = "VREDUCE"
)

// BinOp is an arithmetic/logical binary operator.
type BinOp string

const (
	Add BinOp = "+"
	Sub BinOp = "-"
	Mul BinOp = "*"
	Quo BinOp = "/"
	Rem BinOp = "%"
	And BinOp = "&"
	Or  BinOp = "|"
	Xor BinOp = "^"
)

// CmpOp is a comparison operator producing a mask.
type CmpOp string

const (
	Eq  CmpOp = "=="
	Neq CmpOp = "!="
	Lt  CmpOp = "<"
	Gt  CmpOp = ">"
	Le  CmpOp = "<="
	Ge  CmpOp = ">="
)

// Reg is a vector value register (int64 per lane).
type Reg int

// Mask is a predicate register (bool per lane).
type Mask int

// Instr is one masked-SIMD instruction. Fields not used by an opcode are
// left zero-valued.
type Instr struct {
	Op     Opcode
	StmtID int // source statement order, for fault attribution (-1 if none)

	Dst  Reg    // value destination
	MDst Mask   // mask destination
	Mask Mask   // predicate under which this instruction acts/evaluates
	A    Reg    // first operand
	B    Reg    // second operand
	Imm  int64  // constant / base index
	Name string // array name
	Bop  BinOp
	Cop  CmpOp
	// Reduce-only fields
	Rop    string // "+", "*", "concat"
	Target string // scalar output name
}

// ReduceInstr is a per-lane source gather instruction for the reduce pass.
// It is executed after all body batches, strictly ascending over indices.
type ReduceInstr struct {
	Rop    string
	Target string
	Source string
}

// Program is a lowered masked-SIMD program.
type Program struct {
	Width      int
	IndexVar   string
	BeginConst int64  // counted loops must begin at constant 0
	EndName    string // loop-count array: len(EndName) drives iteration
	Body       []Instr
	Reduces    []ReduceInstr
	NumRegs    int
	NumMasks   int
	// ReductionOrder declares the associativity rule.
	ReductionOrder string
}

// String renders a short summary used in diagnostics.
func (p *Program) String() string {
	return fmtProgram(p)
}

// Sel is a masked blend: dst = mask ? A : B.
const Sel BinOp = "sel"
