package frontend

// TokenKind identifies a lexical token.
type TokenKind int

const (
	TEOF TokenKind = iota
	TIdent
	TInt
	TStr
	// keywords
	KGen
	KVar
	KIf
	KElse
	KWhile
	KTry
	KCatch
	KFinally
	KYield
	KReturn
	KThrow
	KTrue
	KFalse
	KNil
	// punctuation
	TLParen  // (
	TRParen  // )
	TLBrace  // {
	TRBrace  // }
	TComma   // ,
	TSemicol // ;
	TAssign  // =
	TPlus
	TMINUS
	TStar
	TSlash
	TPct
	TEq // ==
	TNe // !=
	TLt
	TLe
	TGt
	TGe
	TAnd // &&
	TOr  // ||
	TBang
)

// Token is a single lexical token with its source position (1-based line,
// 0-based column offset).
type Token struct {
	Kind TokenKind
	Val  string
	Line int
	Col  int
}

var keywords = map[string]TokenKind{
	"gen":     KGen,
	"var":     KVar,
	"if":      KIf,
	"else":    KElse,
	"while":   KWhile,
	"try":     KTry,
	"catch":   KCatch,
	"finally": KFinally,
	"yield":   KYield,
	"return":  KReturn,
	"throw":   KThrow,
	"true":    KTrue,
	"false":   KFalse,
	"nil":     KNil,
}
