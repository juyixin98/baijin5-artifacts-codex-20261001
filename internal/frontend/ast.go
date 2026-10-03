package frontend

type Module struct {
	Name    string
	Imports []string
	Decls   []Decl
}

type Decl interface{ declNode() }

type ConstDecl struct {
	Pub    bool
	Name   string
	Type   string
	Value  Expr
	Inline bool
	Pos    Position
}

type FnDecl struct {
	Pub     bool
	Name    string
	Generic bool
	Params  []Param
	Result  string
	Body    []Stmt
	Pos     Position
}

type Param struct {
	Name string
	Type string
}

func (*ConstDecl) declNode() {}
func (*FnDecl) declNode()    {}

type Stmt interface{ stmtNode() }

type LetStmt struct {
	Name  string
	Type  string
	Value Expr
	Pos   Position
}

type ReturnStmt struct {
	Value Expr
	Has   bool
	Pos   Position
}

type ExprStmt struct {
	Expr Expr
}

type IfStmt struct {
	Cond Expr
	Then []Stmt
	Else []Stmt
	Pos  Position
}

func (*LetStmt) stmtNode()    {}
func (*ReturnStmt) stmtNode() {}
func (*ExprStmt) stmtNode()   {}
func (*IfStmt) stmtNode()     {}

type Expr interface{ exprNode() }

type IntLit struct{ Value int64 }
type StrLit struct{ Value string }
type BoolLit struct{ Value bool }
type IdentExpr struct {
	Name string
}
type CallExpr struct {
	Name string
	Args []Expr
}
type UnaryExpr struct {
	Op    string
	Inner Expr
}
type BinaryExpr struct {
	Op       string
	Lhs, Rhs Expr
}

func (*IntLit) exprNode()     {}
func (*StrLit) exprNode()     {}
func (*BoolLit) exprNode()    {}
func (*IdentExpr) exprNode()  {}
func (*CallExpr) exprNode()   {}
func (*UnaryExpr) exprNode()  {}
func (*BinaryExpr) exprNode() {}
