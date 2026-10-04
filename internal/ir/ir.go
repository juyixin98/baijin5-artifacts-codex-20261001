// Package ir defines the explicit state-machine intermediate representation
// and the AST -> IR transform.
//
// A Routine is a flat instruction array with symbolic jump labels resolved
// after emission. Generator routines contain OYield pause sites; each pause
// site is a numbered state of the machine. A suspended machine frame is
// fully described by (pc, locals, operand stack, handler stack, pending
// unwind signal), which is what makes the transform an explicit state
// machine rather than a syntax tree.
package ir

// OpCode enumerates IR instructions.
type OpCode int

const (
	ONop OpCode = iota
	// Constants.
	OConstNull
	OConstBool
	OConstInt
	OConstStr
	// Locals.
	OLoad
	OStore
	OPop
	// Operators.
	OUnary
	OBinary
	// Calls: OCall invokes a named routine. Generator routines produce a
	// newborn generator value instead of executing the body.
	OCall
	// Control flow.
	OJump
	OBranchFalse
	OBranchTrue
	OReturn // pops value (or null) and finishes the frame
	OThrow  // pops exception and starts unwind
	// Generators.
	OYield // pops value, pauses; resume pushes the injected value
	// Exception / cleanup structure (see compiler docs in compile.go).
	OSetup
	OPopHandler
	OBindCatch
	OEnterFinally
	OEnterFinallyUnwind
	OEndFinally
	// For-iteration over a generator value.
	OSetupIter
	OPopIter
	OIterNext
)

// Handler describes one dynamic entry pushed by OSetup.
type HandlerKind int

const (
	HandlerTry HandlerKind = iota
	HandlerIter
)

type Handler struct {
	Kind      HandlerKind
	CatchLbl  Label // 0 when no catch
	FinLbl    Label // 0 when no finally
	ParamSlot int
	IterSlot  int
	CatchArmed bool
	InCleanup  bool
}

// Op is one IR instruction.
type Op struct {
	C   OpCode
	I   int64  // int const / nargs / slot
	B   bool   // bool const
	S   string // string const / operator / callee
	L1  Label // assembly-time symbolic labels
	L2  Label
	J1  int   // resolved jump PCs (-1 when absent)
	J2  int
	Pos int // ast position encoded line*100000+col
}

// Label is a symbolic jump target resolved by Build.
type Label int

// PausePoint is a numbered state-machine state corresponding to one OYield.
type PausePoint struct {
	ID       int
	PC       int
	Line     int
	Col      int
	NLocals  int
	HandlerN int // handler-stack depth required to resume correctly
}

// Routine is compiled code for one function (generator or plain).
type Routine struct {
	Name    string
	Params  []string
	IsGen   bool
	Code    []Op
	Labels  map[Label]int
	Pauses  []PausePoint
	NSlots  int
	NumLits struct {
		Ints    []int64
		Strs    []string
		Bools   []bool
	}
}

// Program is the compiled whole program.
type Program struct {
	Routines map[string]*Routine
	Order    []string
}
