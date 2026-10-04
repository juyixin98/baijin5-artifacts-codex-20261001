// Package lexer turns source text into a token stream.
package lexer

import (
	"fmt"
	"strings"
	"unicode"

	"genfsm/internal/semerr"
)

type Kind int

const (
	TEOF Kind = iota
	TIdent
	TInt
	TStr
	// keywords
	TFn
	TGen
	TLet
	TIf
	TElse
	TWhile
	TFor
	TIn
	TReturn
	TThrow
	TTry
	TCatch
	TFinally
	TYield
	TNull
	TTrue
	TFalse
	// punctuation
	TLParen
	TRParen
	TLCurly
	TRCurly
	TComma
	TSemi
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
	TPercent
	TBang
	TAnd
	TOr
)

type Token struct {
	Kind Kind
	Val  string
	Line int
	Col  int
}

var keywords = map[string]Kind{
	"fn":      TFn,
	"gen":     TGen,
	"let":     TLet,
	"if":      TIf,
	"else":    TElse,
	"while":   TWhile,
	"for":     TFor,
	"in":      TIn,
	"return":  TReturn,
	"throw":   TThrow,
	"try":     TTry,
	"catch":   TCatch,
	"finally": TFinally,
	"yield":   TYield,
	"null":    TNull,
	"true":    TTrue,
	"false":   TFalse,
}

type Lexer struct {
	src  string
	pos  int
	line int
	col  int
}

func New(src string) *Lexer {
	return &Lexer{src: src, pos: 0, line: 1, col: 1}
}

func (l *Lexer) peek() byte {
	if l.pos >= len(l.src) {
		return 0
	}
	return l.src[l.pos]
}

func (l *Lexer) peekAt(off int) byte {
	if l.pos+off >= len(l.src) {
		return 0
	}
	return l.src[l.pos+off]
}

func (l *Lexer) advance() byte {
	ch := l.src[l.pos]
	l.pos++
	if ch == '\n' {
		l.line++
		l.col = 1
	} else {
		l.col++
	}
	return ch
}

func (l *Lexer) skipSpaceAndComments() {
	for l.pos < len(l.src) {
		ch := l.peek()
		if ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' {
			l.advance()
		} else if ch == '/' && l.peekAt(1) == '/' {
			for l.pos < len(l.src) && l.peek() != '\n' {
				l.advance()
			}
		} else if ch == '#' { // alternate comment marker
			for l.pos < len(l.src) && l.peek() != '\n' {
				l.advance()
			}
		} else {
			return
		}
	}
}

func (l *Lexer) Tokenize() ([]Token, error) {
	var toks []Token
	for {
		l.skipSpaceAndComments()
		if l.pos >= len(l.src) {
			toks = append(toks, Token{Kind: TEOF, Line: l.line, Col: l.col})
			return toks, nil
		}
		line, col := l.line, l.col
		ch := l.peek()
		switch {
		case ch == '_' || unicode.IsLetter(rune(ch)):
			start := l.pos
			for l.pos < len(l.src) {
				c := l.peek()
				if c == '_' || unicode.IsLetter(rune(c)) || unicode.IsDigit(rune(c)) {
					l.advance()
				} else {
					break
				}
			}
			word := l.src[start:l.pos]
			if k, ok := keywords[word]; ok {
				toks = append(toks, Token{Kind: k, Val: word, Line: line, Col: col})
			} else {
				toks = append(toks, Token{Kind: TIdent, Val: word, Line: line, Col: col})
			}
		case unicode.IsDigit(rune(ch)):
			start := l.pos
			for l.pos < len(l.src) && unicode.IsDigit(rune(l.peek())) {
				l.advance()
			}
			toks = append(toks, Token{Kind: TInt, Val: l.src[start:l.pos], Line: line, Col: col})
		case ch == '"':
			s, err := l.lexString()
			if err != nil {
				return nil, err
			}
			toks = append(toks, Token{Kind: TStr, Val: s, Line: line, Col: col})
		default:
			t, err := l.lexPunct()
			if err != nil {
				return nil, err
			}
			toks = append(toks, t)
		}
	}
}

func (l *Lexer) lexString() (string, error) {
	startLine, startCol := l.line, l.col
	l.advance() // opening quote
	var b strings.Builder
	for l.pos < len(l.src) {
		ch := l.advance()
		if ch == '"' {
			return b.String(), nil
		}
		if ch == '\n' {
			return "", semerr.Input(semerr.CodeLex, "unterminated string at %d:%d", startLine, startCol)
		}
		if ch == '\\' {
			if l.pos >= len(l.src) {
				break
			}
			esc := l.advance()
			switch esc {
			case 'n':
				b.WriteByte('\n')
			case 't':
				b.WriteByte('\t')
			case 'r':
				b.WriteByte('\r')
			case '\\':
				b.WriteByte('\\')
			case '"':
				b.WriteByte('"')
			case '0':
				b.WriteByte(0)
			default:
				return "", semerr.Input(semerr.CodeLex, "invalid escape \\%c at %d:%d", esc, l.line, l.col)
			}
			continue
		}
		b.WriteByte(ch)
	}
	return "", semerr.Input(semerr.CodeLex, "unterminated string at %d:%d", startLine, startCol)
}

func (l *Lexer) single(k Kind) (Token, error) {
	line, col := l.line, l.col
	ch := l.advance()
	return Token{Kind: k, Val: string(ch), Line: line, Col: col}, nil
}

func (l *Lexer) lexPunct() (Token, error) {
	line, col := l.line, l.col
	ch := l.peek()
	switch ch {
	case '(':
		return l.single(TLParen)
	case ')':
		return l.single(TRParen)
	case '{':
		return l.single(TLCurly)
	case '}':
		return l.single(TRCurly)
	case ',':
		return l.single(TComma)
	case ';':
		return l.single(TSemi)
	case '+':
		return l.single(TPlus)
	case '-':
		return l.single(TMinus)
	case '*':
		return l.single(TStar)
	case '/':
		return l.single(TSlash)
	case '%':
		return l.single(TPercent)
	case '=':
		l.advance()
		if l.peek() == '=' {
			l.advance()
			return Token{Kind: TEq, Val: "==", Line: line, Col: col}, nil
		}
		return Token{Kind: TAssign, Val: "=", Line: line, Col: col}, nil
	case '!':
		l.advance()
		if l.peek() == '=' {
			l.advance()
			return Token{Kind: TNeq, Val: "!=", Line: line, Col: col}, nil
		}
		return Token{Kind: TBang, Val: "!", Line: line, Col: col}, nil
	case '<':
		l.advance()
		if l.peek() == '=' {
			l.advance()
			return Token{Kind: TLe, Val: "<=", Line: line, Col: col}, nil
		}
		return Token{Kind: TLt, Val: "<", Line: line, Col: col}, nil
	case '>':
		l.advance()
		if l.peek() == '=' {
			l.advance()
			return Token{Kind: TGe, Val: ">=", Line: line, Col: col}, nil
		}
		return Token{Kind: TGt, Val: ">", Line: line, Col: col}, nil
	case '&':
		l.advance()
		if l.peek() == '&' {
			l.advance()
			return Token{Kind: TAnd, Val: "&&", Line: line, Col: col}, nil
		}
		return Token{}, semerr.Input(semerr.CodeLex, "unexpected '&' (did you mean '&&'?) at %d:%d", line, col)
	case '|':
		l.advance()
		if l.peek() == '|' {
			l.advance()
			return Token{Kind: TOr, Val: "||", Line: line, Col: col}, nil
		}
		return Token{}, semerr.Input(semerr.CodeLex, "unexpected '|' (did you mean '||'?) at %d:%d", line, col)
	}
	return Token{}, semerr.Input(semerr.CodeLex, fmt.Sprintf("unexpected character %q at %d:%d", string(ch), line, col))
}
