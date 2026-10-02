package syntax

import (
	"fmt"

	"funcspec/internal/errcat"
)

// Pos is a 1-based byte offset into the source file.
type Pos struct {
	Line int
	Col  int
}

func (p Pos) String() string { return fmt.Sprintf("%d:%d", p.Line, p.Col) }

// TokenKind enumerates lexical token kinds.
type TokenKind int

const (
	TEOF TokenKind = iota
	TIdent
	TInt
	TEq
	TEqEq
	TNotEq
	TLt
	TGt
	TLtEq
	TGtEq
	TPlus
	TMinus
	TStar
	TSlash
	TPercent
	TNot
	TAnd
	TAndAnd
	TOr
	TOrOr
	TLParen
	TRParen
	TLBrace
	TRBrace
	TComma
	TSemicolon
	TBang
)

// Token is a single lexical token.
type Token struct {
	Kind TokenKind
	Lit  string
	Pos  Pos
	// PreSemi is true when Go-style automatic semicolon insertion placed a
	// virtual ";" before this token (newline after a value-ending token).
	PreSemi bool
}

type lexer struct {
	src  string
	i    int
	line int
	col  int
}

// Lex tokenizes src. It implements Go-style automatic semicolon insertion:
// a newline following an identifier, integer literal or ")" / "}" produces a
// virtual semicolon, which lets fixture programs omit statement terminators.
func Lex(src string) ([]Token, error) {
	l := &lexer{src: src, line: 1, col: 1}
	var toks []Token
	lastValEnd := false
	for l.i < len(l.src) {
		c := l.src[l.i]
		switch {
		case c == '\n':
			insert := lastValEnd
			l.advance()
			for l.i < len(l.src) && (l.src[l.i] == '\n' || l.src[l.i] == '\r') {
				l.advance()
			}
			l.skipInlineSpace()
			for l.i < len(l.src) && l.src[l.i] == '/' && l.i+1 < len(l.src) && l.src[l.i+1] == '/' {
				l.skipLineComment()
				for l.i < len(l.src) && (l.src[l.i] == '\n' || l.src[l.i] == '\r') {
					l.advance()
				}
			}
			if insert && !l.closesBlock() && !l.startsDecl() && l.i < len(l.src) {
				toks = append(toks, Token{Kind: TSemicolon, Lit: "\n", Pos: Pos{l.line, l.col}, PreSemi: true})
			}
			lastValEnd = false
			continue
		case c == ' ' || c == '\t' || c == '\r':
			l.advance()
			continue
		case c == '/' && l.i+1 < len(l.src) && l.src[l.i+1] == '/':
			l.skipLineComment()
			continue
		case c == '/' && l.i+1 < len(l.src) && l.src[l.i+1] == '*':
			if err := l.skipBlockComment(); err != nil {
				return nil, err
			}
			continue
		case c == '0' && l.i+1 < len(l.src) && (l.src[l.i+1] == 'x' || l.src[l.i+1] == 'X'):
			t, err := l.lexHex()
			if err != nil {
				return nil, err
			}
			toks = append(toks, t)
			lastValEnd = true
		case c >= '0' && c <= '9':
			toks = append(toks, l.lexNumber())
			lastValEnd = true
		case isIdentStart(c):
			start := l.pos()
			lit := l.lexIdent()
			toks = append(toks, Token{Kind: TIdent, Lit: lit, Pos: start})
			lastValEnd = true
		default:
			t, err := l.lexPunct()
			if err != nil {
				return nil, err
			}
			toks = append(toks, t)
			switch t.Kind {
			case TRParen, TRBrace:
				lastValEnd = true
			default:
				lastValEnd = false
			}
		}
	}
	toks = append(toks, Token{Kind: TEOF, Pos: Pos{l.line, l.col}})
	return toks, nil
}

func (l *lexer) advance() {
	if l.i < len(l.src) {
		if l.src[l.i] == '\n' {
			l.line++
			l.col = 1
		} else {
			l.col++
		}
		l.i++
	}
}

func (l *lexer) pos() Pos { return Pos{l.line, l.col} }

func (l *lexer) skipInlineSpace() {
	for l.i < len(l.src) {
		c := l.src[l.i]
		if c == ' ' || c == '\t' || c == '\r' {
			l.advance()
		} else {
			break
		}
	}
}

func (l *lexer) skipLineComment() {
	for l.i < len(l.src) && l.src[l.i] != '\n' {
		l.advance()
	}
}

func (l *lexer) skipBlockComment() error {
	start := l.pos()
	l.advance()
	l.advance()
	for l.i < len(l.src) {
		if l.src[l.i] == '*' && l.i+1 < len(l.src) && l.src[l.i+1] == '/' {
			l.advance()
			l.advance()
			return nil
		}
		l.advance()
	}
	return errcat.New(errcat.Syntax, "unterminated block comment at %s", start)
}

func (l *lexer) closesBlock() bool {
	// A virtual semicolon right before } is redundant.
	return l.i < len(l.src) && l.src[l.i] == '}'
}

func (l *lexer) lexIdent() string {
	start := l.i
	for l.i < len(l.src) && isIdentPart(l.src[l.i]) {
		l.advance()
	}
	return l.src[start:l.i]
}

func (l *lexer) lexNumber() Token {
	start := l.pos()
	s := l.i
	for l.i < len(l.src) && l.src[l.i] >= '0' && l.src[l.i] <= '9' {
		l.advance()
	}
	return Token{Kind: TInt, Lit: l.src[s:l.i], Pos: start}
}

func (l *lexer) lexHex() (Token, error) {
	start := l.pos()
	s := l.i
	l.advance()
	l.advance()
	for l.i < len(l.src) && isHexDigit(l.src[l.i]) {
		l.advance()
	}
	lit := l.src[s:l.i]
	if len(lit) == 2 {
		return Token{}, errcat.New(errcat.Syntax, "malformed hex literal at %s", start)
	}
	return Token{Kind: TInt, Lit: lit, Pos: start}, nil
}

func (l *lexer) lexPunct() (Token, error) {
	start := l.pos()
	c := l.src[l.i]
	two := ""
	if l.i+1 < len(l.src) {
		two = l.src[l.i : l.i+2]
	}
	match := func(kind TokenKind, lit string) Token {
		for range lit {
			l.advance()
		}
		return Token{Kind: kind, Lit: lit, Pos: start}
	}
	switch two {
	case "==":
		return match(TEqEq, two), nil
	case "!=":
		return match(TNotEq, two), nil
	case "<=":
		return match(TLtEq, two), nil
	case ">=":
		return match(TGtEq, two), nil
	case "&&":
		return match(TAndAnd, two), nil
	case "||":
		return match(TOrOr, two), nil
	}
	switch c {
	case '=':
		return match(TEq, "="), nil
	case '<':
		return match(TLt, "<"), nil
	case '>':
		return match(TGt, ">"), nil
	case '+':
		return match(TPlus, "+"), nil
	case '-':
		return match(TMinus, "-"), nil
	case '*':
		return match(TStar, "*"), nil
	case '/':
		return match(TSlash, "/"), nil
	case '%':
		return match(TPercent, "%"), nil
	case '!':
		return match(TNot, "!"), nil
	case '(':
		return match(TLParen, "("), nil
	case ')':
		return match(TRParen, ")"), nil
	case '{':
		return match(TLBrace, "{"), nil
	case '}':
		return match(TRBrace, "}"), nil
	case ',':
		return match(TComma, ","), nil
	case ';':
		return match(TSemicolon, ";"), nil
	}
	return Token{}, errcat.New(errcat.Syntax, "unexpected character %q at %s", string(c), start)
}

func isIdentStart(c byte) bool {
	return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
}

func isIdentPart(c byte) bool {
	return isIdentStart(c) || (c >= '0' && c <= '9')
}

func isHexDigit(c byte) bool {
	return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
}

// startsDecl reports whether the upcoming identifier token begins a new
// top-level function declaration ("fn" or "pure"), in which case the virtual
// semicolon after a preceding "}" must be suppressed.
func (l *lexer) startsDecl() bool {
	j := l.i
	rest := l.src[j:]
	for _, kw := range []string{"fn", "pure"} {
		if len(rest) >= len(kw) && rest[:len(kw)] == kw {
			after := byte(' ')
			if len(rest) > len(kw) {
				after = rest[len(kw)]
			}
			if !isIdentPart(after) {
				return true
			}
		}
	}
	return false
}
