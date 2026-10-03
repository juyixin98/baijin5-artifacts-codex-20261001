package frontend

import "fmt"

// Kind enumerates the lexical token kinds of RL ("restricted language").
type Kind int

const (
	TEOF Kind = iota
	TIdent
	TInt
	TStr
	// keywords
	TPackage
	TImport
	TConst
	TFn
	TType
	TIf
	TEElse
	TVar
	TReturn
	TSensitive
	// punctuation
	TLParen
	TRParen
	TLBrace
	TRBrace
	TLBrack
	TRBrack
	TComma
	TColon
	TQual
	TAssign
	TEq
	TNeq
	TLt
	TLe
	TGt
	TGe
	TPlus
	TMinus
	TStar
	TSlash
)

type Token struct {
	Kind Kind
	Lit  string
	Line int
	Col  int
}

var keywords = map[string]Kind{
	"package":   TPackage,
	"import":    TImport,
	"const":     TConst,
	"fn":        TFn,
	"type":      TType,
	"if":        TIf,
	"else":      TEElse,
	"var":       TVar,
	"return":    TReturn,
	"sensitive": TSensitive,
}

func (k Kind) String() string {
	switch k {
	case TEOF:
		return "EOF"
	case TIdent:
		return "identifier"
	case TInt:
		return "int literal"
	case TStr:
		return "string literal"
	default:
		for lit, kk := range keywords {
			if kk == k {
				return fmt.Sprintf("%q", lit)
			}
		}
		return punctuationName(k)
	}
}

func punctuationName(k Kind) string {
	switch k {
	case TLParen:
		return `"("`
	case TRParen:
		return `")"`
	case TLBrace:
		return `"{"`
	case TRBrace:
		return `"}"`
	case TLBrack:
		return `"["`
	case TRBrack:
		return `"]"`
	case TComma:
		return `","`
	case TColon:
		return `":"`
	case TAssign:
		return `"="`
	case TEq:
		return `"=="`
	case TNeq:
		return `"!="`
	case TLt:
		return `"<"`
	case TLe:
		return `"<="`
	case TGt:
		return `">"`
	case TGe:
		return `">="`
	case TPlus:
		return `"+"`
	case TMinus:
		return `"-"`
	case TStar:
		return `"*"`
	case TSlash:
		return `"/"`
	default:
		return "?"
	}
}
