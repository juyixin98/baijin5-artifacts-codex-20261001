package front

// Node is any syntax element carrying its source position.
type Node struct {
	Line int
	Col  int
}

// FnDecl is one top-level function declaration.
type FnDecl struct {
	Node
	Name           string
	Pure           bool
	ExplicitImpure bool
	Params         []Param
	Body           Expr
}

// Param is a formal parameter; Static is the binding-time annotation.
type Param struct {
	Node
	Name   string
	Static bool
}

// Program is the parsed source.
type Program struct {
	Order []string
	Funcs map[string]*FnDecl
}

// Expr is the AST expression interface.
type Expr interface{ exprNode() }

type IntLit struct {
	Node
	Value int64
}

type BoolLit struct {
	Node
	Value bool
}

type VarRef struct {
	Node
	Name string
}

type Unary struct {
	Node
	Op string
	X  Expr
}

type Binary struct {
	Node
	Op       string
	Lhs, Rhs Expr
}

type IfExpr struct {
	Node
	Cond, Then, Else Expr
}

type LetExpr struct {
	Node
	Name  string
	Bound Expr
	Body  Expr
}

type CallExpr struct {
	Node
	Name string
	Args []Expr
}

func (*IntLit) exprNode()   {}
func (*BoolLit) exprNode()  {}
func (*VarRef) exprNode()   {}
func (*Unary) exprNode()    {}
func (*Binary) exprNode()   {}
func (*IfExpr) exprNode()   {}
func (*LetExpr) exprNode()  {}
func (*CallExpr) exprNode() {}
