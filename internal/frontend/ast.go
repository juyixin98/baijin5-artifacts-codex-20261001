package frontend

// Program is one RL source file.
type Program struct {
	Module  string
	Imports []string
	Clauses []Clause
}

// Clause is a top-level declaration.
type Clause struct {
	Line      int
	Sensitive bool // @sensitive: only legal on const; recorded by the parser.
	Const     *ConstDecl
	Type      *TypeDecl
	Func      *FuncDecl
}

type ConstDecl struct {
	Name string
	Type TypeRef
	Init Expr
}

type TypeDecl struct {
	Name string
	Base TypeRef
}

type FuncDecl struct {
	Name       string
	TypeParams []string
	Params     []Param
	Result     TypeRef // zero TypeRef means no result
	Body       []Stmt
}

type Param struct {
	Name string
	Type TypeRef
}

// TypeRef is a named type. Module is non-empty for qualified "module.Name".
type TypeRef struct {
	Module string
	Name   string
}

type Stmt struct {
	Line   int
	Var    *VarStmt
	Return *ReturnStmt
	If     *IfStmt
	Expr   ExprStmt
}

type VarStmt struct {
	Name    string
	Type    TypeRef
	Init    *Expr // optional
	HasInit bool
}

type ReturnStmt struct {
	Value *Expr // optional
}

type IfStmt struct {
	Cond Expr
	Then []Stmt
	Else []Stmt
}

type ExprStmt struct {
	Value Expr
}

type Expr struct {
	Line int
	// exactly one of the following is set
	Lit    *Literal
	Var    *Variable
	Call   *CallExpr
	Unary  *UnaryExpr
	Binary *BinaryExpr
}

type Literal struct {
	Kind   LitKind
	IntVal int64
	StrVal string
}

type LitKind int

const (
	LitInt LitKind = iota
	LitStr
)

type Variable struct {
	LineNo int
	Module string
	Name   string
}

func (v *Variable) Line() int { return v.LineNo }

type CallExpr struct {
	Module   string
	Name     string
	TypeArgs []TypeRef
	Args     []Expr
}

type UnaryExpr struct {
	Op    string // "-"
	Inner Expr
}

type BinaryExpr struct {
	Op          string
	Left, Right Expr
}
