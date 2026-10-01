package frontend

// Program is a set of named generator declarations.
type Program struct {
	Gens []*GenDecl
}

// GenDecl is a parameterless generator: gen name { body }.
type GenDecl struct {
	Name string
	Line int
	Body []Stmt
}

// Pos records a 1-based source line.
type Pos struct{ Line int }

// Stmt is a statement AST node.
type Stmt interface{ stmtPos() Pos }

type BlockStmt struct {
	Pos
	List []Stmt
}

type VarStmt struct {
	Pos
	Name string
	Init Expr // may be nil
}

type AssignStmt struct {
	Pos
	Name string
	Expr Expr
}

type ExprStmt struct {
	Pos
	Expr Expr
}

// YieldStmt: yield expr; (expr optional, defaults to nil value).
type YieldStmt struct {
	Pos
	Expr Expr
}

type ReturnStmt struct {
	Pos
	Expr Expr // optional
}

type ThrowStmt struct {
	Pos
	Expr Expr
}

type IfStmt struct {
	Pos
	Cond Expr
	Then []Stmt
	Else []Stmt // optional
}

type WhileStmt struct {
	Pos
	Cond Expr
	Body []Stmt
}

type CatchClause struct {
	Pos
	Name string // bound variable for the caught value
	Test Expr   // optional filter; nil catches every (Exception-equivalent)
	Body []Stmt
}

type TryStmt struct {
	Pos
	Body    []Stmt
	Catches []*CatchClause // zero or more
	Finally []Stmt         // optional
}

func (s *BlockStmt) stmtPos() Pos  { return s.Pos }
func (s *VarStmt) stmtPos() Pos    { return s.Pos }
func (s *AssignStmt) stmtPos() Pos { return s.Pos }
func (s *ExprStmt) stmtPos() Pos   { return s.Pos }
func (s *YieldStmt) stmtPos() Pos  { return s.Pos }
func (s *ReturnStmt) stmtPos() Pos { return s.Pos }
func (s *ThrowStmt) stmtPos() Pos  { return s.Pos }
func (s *IfStmt) stmtPos() Pos     { return s.Pos }
func (s *WhileStmt) stmtPos() Pos  { return s.Pos }
func (s *TryStmt) stmtPos() Pos    { return s.Pos }

// Expr is an expression AST node.
type Expr interface{ exprPos() Pos }

type IntLit struct {
	Pos
	Value int64
}

type StrLit struct {
	Pos
	Value string
}

type BoolLit struct {
	Pos
	Value bool
}

type NilLit struct{ Pos }

type Ident struct {
	Pos
	Name string
}

type UnaryExpr struct {
	Pos
	Op   string
	Expr Expr
}

type BinaryExpr struct {
	Pos
	Op       string
	LHS, RHS Expr
}

// CallExpr covers built-in calls: log(...).
type CallExpr struct {
	Pos
	Name string
	Args []Expr
}

func (e *IntLit) exprPos() Pos     { return e.Pos }
func (e *StrLit) exprPos() Pos     { return e.Pos }
func (e *BoolLit) exprPos() Pos    { return e.Pos }
func (e *NilLit) exprPos() Pos     { return e.Pos }
func (e *Ident) exprPos() Pos      { return e.Pos }
func (e *UnaryExpr) exprPos() Pos  { return e.Pos }
func (e *BinaryExpr) exprPos() Pos { return e.Pos }
func (e *CallExpr) exprPos() Pos   { return e.Pos }
