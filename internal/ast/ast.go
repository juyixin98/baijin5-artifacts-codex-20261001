// Package ast defines the abstract syntax tree of ScopeLang.
package ast

// Stmt is implemented by every statement node.
type Stmt interface{ stmtNode() }

// Root is the whole program: a single implicit outer block.
type Root struct {
	Body *Block
}

// Block is a brace-delimited statement sequence, or the program body.
type Block struct {
	Stmts  []Stmt
	LBrace Pos
	RBrace Pos
	// Named is true for explicit `scope { ... }` blocks.
	Named bool
}

// Acquire binds a named resource: let name = acquire(expr) onexit { ... }.
type Acquire struct {
	Name    string
	ResExpr string
	Point   string // stable injection point, e.g. init@2 (assigned by parser)
	Line    int
	Start   Pos
	Cleanup *Block // statements allowed: emit/fail only
}

// Emit appends a string to the observable trace.
type Emit struct {
	Text  string
	Point string // stable fail point, e.g. fail@3
	Line  int
	Pos   Pos
}

// Fail raises an injected (or default) error. Failures is nil when the
// point is synthetically injected; explicit fail uses default Computation.
type Fail struct {
	Point string // stable fail point, e.g. fail@4 or close@2
	Line  int
	Pos   Pos
}

// Break exits the innermost repeat loop after running cleanups.
type Break struct {
	Line int
	Pos  Pos
}

// Return unwinds the whole program with a value after running cleanups.
type Return struct {
	Value string // may be "" for a bare return
	Line  int
	Pos   Pos
}

// Repeat runs its body N times in nested per-iteration scopes.
type Repeat struct {
	Count int
	Body  *Block
	Line  int
	Pos   Pos
}

func (*Block) stmtNode()   {}
func (*Acquire) stmtNode() {}
func (*Emit) stmtNode()    {}
func (*Fail) stmtNode()    {}
func (*Break) stmtNode()   {}
func (*Return) stmtNode()  {}
func (*Repeat) stmtNode()  {}

// Pos records a source location for diagnostics.
type Pos struct {
	Line   int
	Column int
	Off    int
}
