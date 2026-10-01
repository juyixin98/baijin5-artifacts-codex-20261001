// Package ir defines the first-order intermediate representation produced by
// the language frontend and consumed by the reference interpreter and the
// partial evaluator.
package ir

// Expr is an IR expression. Values are 64-bit signed integers; booleans are
// encoded as 1/0.
type Expr interface{ irExpr() }

type Int struct{ Value int64 }
type Var struct{ Name string }

type Unary struct {
	Op string
	X  Expr
}
type Binary struct {
	Op       string
	Lhs, Rhs Expr
}
type If struct {
	Cond, Then, Else Expr
}
type Let struct {
	Name  string
	Bound Expr
	Body  Expr
}
type Call struct {
	Name string
	Args []Expr
}

func (*Int) irExpr()    {}
func (*Var) irExpr()    {}
func (*Unary) irExpr()  {}
func (*Binary) irExpr() {}
func (*If) irExpr()     {}
func (*Let) irExpr()    {}
func (*Call) irExpr()   {}

// Builtin effectful operations.
const (
	PrintName = "print"
	ReadName  = "read"
)

// Param is a formal parameter with its binding-time annotation.
type Param struct {
	Name   string
	Static bool
}

// Func is a first-order function definition.
type Func struct {
	Name   string
	Pure   bool
	Params []Param
	Body   Expr
	Line   int
	Col    int
}

// Program is a validated IR program.
type Program struct {
	Funcs map[string]*Func
	Order []string
}

// Arity returns the number of formal parameters.
func (f *Func) Arity() int { return len(f.Params) }
