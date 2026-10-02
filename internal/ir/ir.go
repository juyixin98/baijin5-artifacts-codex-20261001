// Package ir defines the first-order intermediate representation that both
// the interpreter and the specializer operate on.
package ir

import "funcspec/internal/syntax"

// EmitName is the only impure builtin. It appends a tagged effect to the
// interpreter trace and returns 0.
const EmitName = "emit"

// Value is a runtime / compile time value. Only int64 and bool are supported.
type Value struct {
	I int64
	B bool
	// Kind is 'i' or 'b'.
	Kind byte
}

// Int returns an integer value.
func Int(x int64) Value { return Value{I: x, Kind: 'i'} }

// Bool returns a boolean value.
func Bool(x bool) Value { return Value{B: x, Kind: 'b'} }

// Expr is an IR expression node.
type Expr interface{ exprNode() }

// Lit is a literal value.
type Lit struct{ V Value }

// Var is a named local or argument.
type Var struct{ Name string }

// Unary is a prefix operation: "-" or "!".
type Unary struct {
	Op string
	X  Expr
}

// Binary is an infix operation.
type Binary struct {
	Op   string
	X, Y Expr
}

// Call is a call to a user function or to the impure "emit" builtin.
type Call struct {
	Callee string
	Args   []Expr
	Pos    syntax.Pos
}

// SpecializedCall is a call produced by partial evaluation before residual
// assembly. Src is the original callee name; Args holds the reduced argument
// expressions in source order. Static[i] tells whether argument i is a known
// compile-time value (SVal[i]), in which case Args[i] is its literal form.
// The specializer resolves the final target variant and argument projection at
// assembly time, which keeps cross-variant arity alignment in one place.
type SpecializedCall struct {
	Src    string
	Args   []Expr
	Static []bool
	SVal   []Value
	Pos    syntax.Pos
}

// Stmt is an IR statement node.
type Stmt interface{ stmtNode() }

// Let binds Name to Init in the following scope.
type Let struct {
	Name string
	Init Expr
}

// If evaluates Cond and runs Then or Else (Else may be nil).
type If struct {
	Cond Expr
	Then []Stmt
	Else []Stmt
	Pos  syntax.Pos
}

// Return hands Value back to the caller (Value nil means bare return => 0).
type Return struct{ Value Expr }

// ExprStmt evaluates an expression for its side effects.
type ExprStmt struct{ X Expr }

// Func is a lowered, type resolved function.
type Func struct {
	Name   string
	Params []string
	Pure   bool
	Body   []Stmt
}

// Program is a collection of named functions.
type Program struct {
	Funcs map[string]*Func
	Order []string
}

func (*Lit) exprNode()             {}
func (*Var) exprNode()             {}
func (*Unary) exprNode()           {}
func (*Binary) exprNode()          {}
func (*Call) exprNode()            {}
func (*SpecializedCall) exprNode() {}

func (*Let) stmtNode()      {}
func (*If) stmtNode()       {}
func (*Return) stmtNode()   {}
func (*ExprStmt) stmtNode() {}
