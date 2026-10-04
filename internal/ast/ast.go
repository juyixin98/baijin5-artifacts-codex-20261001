// Package ast declares the syntax tree produced by the language frontend.
// The same AST feeds both engines: the direct tree-walking interpreter
// (interp) and the generator-to-state-machine transform (ir + vm).
package ast

import "fmt"

// Pos is a 1-based source position for error messages.
type Pos struct {
	Line int
	Col  int
}

func (p Pos) String() string { return fmt.Sprintf("%d:%d", p.Line, p.Col) }

// Node is implemented by every AST node.
type Node interface{ Pos() Pos }

type Program struct {
	Funcs []*Func
	P     Pos
}

func (n *Program) Pos() Pos { return n.P }

type Func struct {
	Name   string
	Params []string
	IsGen  bool
	Body   *Block
	P      Pos
}

func (n *Func) Pos() Pos { return n.P }

type Block struct {
	Stmts []Stmt
	P     Pos
}

func (n *Block) Pos() Pos { return n.P }
func (*Block) stmtNode()  {}

// Stmt nodes.
type Stmt interface {
	Node
	stmtNode()
}

type LetStmt struct {
	Name string
	Init Expr // may be nil
	P    Pos
}

func (n *LetStmt) Pos() Pos { return n.P }
func (*LetStmt) stmtNode()  {}

type AssignStmt struct {
	Target Expr // NameExpr
	Value  Expr
	P      Pos
}

func (n *AssignStmt) Pos() Pos { return n.P }
func (*AssignStmt) stmtNode()  {}

type ExprStmt struct {
	X Expr
	P Pos
}

func (n *ExprStmt) Pos() Pos { return n.P }
func (*ExprStmt) stmtNode()  {}

type IfStmt struct {
	Cond Expr
	Then *Block
	Else Stmt // *Block or *IfStmt or nil
	P    Pos
}

func (n *IfStmt) Pos() Pos { return n.P }
func (*IfStmt) stmtNode()  {}

type WhileStmt struct {
	Cond Expr
	Body *Block
	P    Pos
}

func (n *WhileStmt) Pos() Pos { return n.P }
func (*WhileStmt) stmtNode()  {}

type ForStmt struct {
	Var  string
	Iter Expr // must evaluate to a generator
	Body *Block
	P    Pos
}

func (n *ForStmt) Pos() Pos { return n.P }
func (*ForStmt) stmtNode()  {}

type ReturnStmt struct {
	Value Expr // nil means bare return
	P     Pos
}

func (n *ReturnStmt) Pos() Pos { return n.P }
func (*ReturnStmt) stmtNode()  {}

type ThrowStmt struct {
	Value Expr
	P     Pos
}

func (n *ThrowStmt) Pos() Pos { return n.P }
func (*ThrowStmt) stmtNode()  {}

type TryStmt struct {
	Body   *Block
	Catch  *CatchClause // nil if no catch
	Finally *Block      // nil if no finally
	P      Pos
}

func (n *TryStmt) Pos() Pos { return n.P }
func (*TryStmt) stmtNode()  {}

type CatchClause struct {
	Param string
	Body  *Block
	P     Pos
}

func (n *CatchClause) Pos() Pos { return n.P }

// Break/continue are intentionally unsupported; return/finally cover the
// required semantics and keeps the transform tractable (see README).

// Expr nodes.
type Expr interface {
	Node
	exprNode()
}

type NullLit struct{ P Pos }

func (n *NullLit) Pos() Pos { return n.P }
func (*NullLit) exprNode()  {}

type BoolLit struct {
	Value bool
	P     Pos
}

func (n *BoolLit) Pos() Pos { return n.P }
func (*BoolLit) exprNode()  {}

type IntLit struct {
	Value int64
	P     Pos
}

func (n *IntLit) Pos() Pos { return n.P }
func (*IntLit) exprNode()  {}

type StrLit struct {
	Value string
	P     Pos
}

func (n *StrLit) Pos() Pos { return n.P }
func (*StrLit) exprNode()  {}

type NameExpr struct {
	Name string
	P    Pos
}

func (n *NameExpr) Pos() Pos { return n.P }
func (*NameExpr) exprNode()  {}

type UnaryExpr struct {
	Op    string
	X     Expr
	P     Pos
}

func (n *UnaryExpr) Pos() Pos { return n.P }
func (*UnaryExpr) exprNode()  {}

type BinaryExpr struct {
	Op          string
	X, Y        Expr
	P           Pos
	ShortCircuit bool // && or ||
}

func (n *BinaryExpr) Pos() Pos { return n.P }
func (*BinaryExpr) exprNode()  {}

type CallExpr struct {
	Callee string // only named global functions are callable
	Args   []Expr
	P      Pos
}

func (n *CallExpr) Pos() Pos { return n.P }
func (*CallExpr) exprNode()  {}

// YieldExpr pauses a generator. Init may be nil (yield).
// The value sent/resumed by the driver is the expression's value.
type YieldExpr struct {
	Init Expr
	P    Pos
}

func (n *YieldExpr) Pos() Pos { return n.P }
func (*YieldExpr) exprNode()  {}
