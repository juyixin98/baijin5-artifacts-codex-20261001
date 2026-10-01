// Package ir defines the explicit state-machine representation produced by
// the frontend-to-CFG transform.
//
// A compiled generator is a graph of basic blocks. Each block holds a
// straight-line list of operations followed by one terminator. Suspension is
// represented explicitly by the Yield terminator, which records the resume
// block, the saved expression-environment height and the saved handler stack.
// Cleanup (finally) code is reachable in two ways:
//
//   - normal fall-through / branch (kind handlerNormal), and
//   - exceptional unwind (handlerCatch / handlerFinally while raising).
//
// The runtime never walks the AST; it only executes this graph.
package ir

import (
	"genstatemachine/internal/frontend"
)

// OpKind enumerates flat, ordered operations inside a block.
type OpKind int

const (
	OpNop    OpKind = iota
	OpConst         // R = const value
	OpLoad          // R = locals[Name]
	OpStore         // locals[Name] = R
	OpUnary         // R = unary OP R
	OpBinary        // R = R1 OP R2
	OpLog           // R = log(R...)
	// OpBindCatch writes the in-flight raised value into locals[Name].
	OpBindCatch

	// OpPushHandler installs an exception/exit handler frame.
	OpPushHandler
	// OpPopHandler removes the most recently installed frame.
	OpPopHandler
	// OpSetCatchMode marks the active frame as executing its catch body, so
	// raises in the catch body are not re-caught by the same frame.
	OpSetCatchMode
	// OpSetFinallyMode marks the active frame as executing its finally body.
	OpSetFinallyMode
	// OpEndFinally dispatches the pending abrupt (normal continuation or
	// further unwind) after the finally body finished.
	OpEndFinally
)

// HandlerKind selects how an installed frame reacts to raises.
type HandlerKind int

const (
	// HandlerCatch routes matching raised user exceptions to CatchBlock and
	// any other abrupt (other exceptions, closing, return) to FinallyBlock.
	HandlerCatch HandlerKind = iota
	// HandlerFinally routes every abrupt to FinallyBlock.
	HandlerFinally
)

// Handler is a single entry in the saved handler stack.
type Handler struct {
	Kind         HandlerKind
	CatchVar     string
	HasFilter    bool
	FilterReg    int  // register holding the filter expression value
	FinallyBlock int  // block index of the finally clause (-1 for catch-only)
	AfterBlock   int  // continuation after successful cleanup
	CatchBlock   int  // block index of the matching catch body (-1 if none)
	InCatch      bool // true when entered from catch-body chaining
}

// Op is one ordered side effect or register operation.
type Op struct {
	Kind    OpKind
	Line    int
	Name    string // store/load/bind/log unused-name
	Op      string // arithmetic operator
	Const   Value
	R       int // destination or only register
	R1, R2  int // source registers for binary
	Handler Handler
	ArgRegs []int
}

// TermKind enumerates block terminators.
type TermKind int

const (
	TermJump   TermKind = iota
	TermBranch          // if Reg then Target else Other
	TermYield           // suspend; Reg is the produced value
	TermReturn          // finish; Reg optional value
	TermRaise           // user throw; Reg holds value
	TermExit            // runtime initiated closing (GeneratorExit equivalent)
	TermHalt            // end of program with no value
)

// Term is the block terminator.
type Term struct {
	Kind   TermKind
	Line   int
	Reg    int
	Target int
	Other  int
	// Yield metadata: when suspended, the machine restores handlers and
	// resumes at Target. EnvHeight is unused (locals are named), kept for
	// readable dumps.
	EnvHeight int
}

// Block is a basic block.
type Block struct {
	Name string
	Ops  []Op
	Term Term
}

// GenIR is one compiled generator.
type GenIR struct {
	Name   string
	Blocks []*Block
	Entry  int
	// SrcGen kept for tooling/tests; not executed by the runtime.
	SrcGen *frontend.GenDecl
}

// Program is a set of compiled generators keyed by name.
type Program struct {
	Gens  map[string]*GenIR
	Order []string
}

// Value mirrors the language's tagged value domain. The compiler only emits
// OpConst values; expressions otherwise live in runtime registers.
type Value struct {
	Kind ValueKind
	I    int64
	S    string
	B    bool
}

type ValueKind int

const (
	VNil ValueKind = iota
	VInt
	VStr
	VBool
)
