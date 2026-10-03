package frontend

type TokenKind int

const (
	TEOF TokenKind = iota
	TIdent
	TInt
	TStr
	TLParen
	TRParen
	LBrace
	RBrace
	TComma
	TColon
	TColonColon
	TSemi
	TArrow
	TPlus
	TMinus
	TStar
	TSlash
	TEq
	TNotEq
	TLt
	TLe
	TGt
	TGe
	TMod
	TAssign
)

type Token struct {
	Kind  TokenKind
	Value string
	Pos   Position
}

type Position struct {
	Line int
	Col  int
	Off  int
}

var keywords = map[string]TokenKind{
	"module":  kwModule,
	"import":  kwImport,
	"pub":     kwPub,
	"fn":      kwFn,
	"let":     kwLet,
	"return":  kwReturn,
	"if":      kwIf,
	"else":    kwElse,
	"true":    kwTrue,
	"false":   kwFalse,
	"int":     kwIntTy,
	"string":  kwStrTy,
	"bool":    kwBoolTy,
	"generic": kwGeneric,
	"const":   kwConst,
}

const (
	kwModule TokenKind = 1000 + iota
	kwImport
	kwPub
	kwFn
	kwLet
	kwReturn
	kwIf
	kwElse
	kwTrue
	kwFalse
	kwIntTy
	kwStrTy
	kwBoolTy
	kwGeneric
	kwConst
)
