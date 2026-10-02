// Package frontend implements the scalar-condition loop language: a lexer,
// parser and semantic checker that produce an AST for the SIMD lowering pass.
package frontend

import "fmt"

// Program is a whole translation unit.
type Program struct {
	Inputs  []*Decl
	Outputs []*Decl
	Body    []Stmt
	Reduces []*ReduceStmt
}

// Kind identifies a declaration class.
type Kind int

const (
	KindScalar Kind = iota
	KindArray
)

func (k Kind) String() string {
	switch k {
	case KindScalar:
		return "scalar"
	case KindArray:
		return "array"
	default:
		return "?"
	}
}

// Decl is an input or output declaration.
type Decl struct {
	Name   string
	Kind   Kind
	Length Expr // non-nil for arrays; must be a constant integer expression
}

// Node position in source, 1-based, used in diagnostics.
type Pos struct {
	Line int
	Col  int
}

func (p Pos) String() string { return fmt.Sprintf("%d:%d", p.Line, p.Col) }

// Stmt is a body statement.
type Stmt interface{ stmtPos() Pos }

// AssignStmt writes to an output scalar or output array element.
type AssignStmt struct {
	Name string
	Idx  Expr // nil for scalar outputs
	Pos  Pos
	Rhs  Expr
}

func (a *AssignStmt) stmtPos() Pos { return a.Pos }

// IfStmt is a scalar conditional.
type IfStmt struct {
	Cond Expr
	Pos  Pos
	Then []Stmt
	Else []Stmt
}

func (s *IfStmt) stmtPos() Pos { return s.Pos }

// ForStmt is the single, non-nested counted loop: for i := 0 .. n { ... }.
type ForStmt struct {
	Var   string
	Begin Expr
	End   Expr // exclusive upper bound
	Pos   Pos
	Body  []Stmt
}

func (f *ForStmt) stmtPos() Pos { return f.Pos }

// ReduceStmt reduces an input array with an order-sensitive operator.
//
//	for i := 0 .. len(a) { reduce x op a[i] }
type ReduceStmt struct {
	LoopVar string
	Begin   Expr
	End     Expr // exclusive
	Pos     Pos
	Target  string
	Op      string // "+", "*", "concat"
	Source  string
	SrcIdx  Expr
}

// Expr is an expression node.
type Expr interface{ exprPos() Pos }

type IntLit struct {
	Value int64
	Pos   Pos
}

func (e *IntLit) exprPos() Pos { return e.Pos }

type Ident struct {
	Name string
	Pos  Pos
}

func (e *Ident) exprPos() Pos { return e.Pos }

// IndexExpr is an array read a[i].
type IndexExpr struct {
	Name string
	Idx  Expr
	Pos  Pos
}

func (e *IndexExpr) exprPos() Pos { return e.Pos }

// LenExpr is len(a), an array length query (int64).
type LenExpr struct {
	Name string
	Pos  Pos
}

func (e *LenExpr) exprPos() Pos { return e.Pos }

type UnaryExpr struct {
	Op    string // "-", "!", "~"
	Inner Expr
	Pos   Pos
}

func (e *UnaryExpr) exprPos() Pos { return e.Pos }

type BinaryExpr struct {
	Op          string
	Left, Right Expr
	Pos         Pos
}

func (e *BinaryExpr) exprPos() Pos { return e.Pos }
