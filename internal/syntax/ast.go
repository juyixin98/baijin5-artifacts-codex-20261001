package syntax

// Program is a source file: a sequence of function declarations.
type Program struct {
	Funcs []*FuncDecl
}

// FuncDecl is `pure? fn name(params) { body }`.
type FuncDecl struct {
	Name   string
	Params []string
	Pure   bool
	Body   []Stmt
	Pos    Pos
}

// Stmt is an AST statement.
type Stmt interface {
	Node
	stmt()
}

// LetStmt is `name = expr`.
type LetStmt struct {
	Name string
	Init Expr
	Pos  Pos
}

// ReturnStmt is `return expr`.
type ReturnStmt struct {
	Value Expr // nil for bare return
	Pos   Pos
}

// IfStmt is `if cond { then } else { else }?`.
type IfStmt struct {
	Cond Expr
	Then []Stmt
	Else []Stmt // nil when absent
	Pos  Pos
}

// ExprStmt is an expression used as a statement (used for emit(...)).
type ExprStmt struct {
	X   Expr
	Pos Pos
}

// Expr is an AST expression.
type Expr interface {
	Node
	expr()
}

// IntLit is an integer literal (decimal or hex).
type IntLit struct {
	Value int64
	Raw   string
	Pos   Pos
}

// BoolLit is true / false.
type BoolLit struct {
	Value bool
	Pos   Pos
}

// Ident is a variable or function reference.
type Ident struct {
	Name string
	Pos  Pos
}

// CallExpr is `callee(args...)`.
type CallExpr struct {
	Callee string
	Args   []Expr
	Pos    Pos
}

// UnaryExpr is `op x`.
type UnaryExpr struct {
	Op  string
	X   Expr
	Pos Pos
}

// BinaryExpr is `x op y`.
type BinaryExpr struct {
	Op   string
	X, Y Expr
	Pos  Pos
}

// Node is implemented by every AST node.
type Node interface{ PosOf() Pos }

func (s *LetStmt) PosOf() Pos    { return s.Pos }
func (s *ReturnStmt) PosOf() Pos { return s.Pos }
func (s *IfStmt) PosOf() Pos     { return s.Pos }
func (s *ExprStmt) PosOf() Pos   { return s.Pos }
func (e *IntLit) PosOf() Pos     { return e.Pos }
func (e *BoolLit) PosOf() Pos    { return e.Pos }
func (e *Ident) PosOf() Pos      { return e.Pos }
func (e *CallExpr) PosOf() Pos   { return e.Pos }
func (e *UnaryExpr) PosOf() Pos  { return e.Pos }
func (e *BinaryExpr) PosOf() Pos { return e.Pos }

func (*LetStmt) stmt()    {}
func (*ReturnStmt) stmt() {}
func (*IfStmt) stmt()     {}
func (*ExprStmt) stmt()   {}

func (*IntLit) expr()     {}
func (*BoolLit) expr()    {}
func (*Ident) expr()      {}
func (*CallExpr) expr()   {}
func (*UnaryExpr) expr()  {}
func (*BinaryExpr) expr() {}
