// Package lexer tokenizes ScopeLang source.
package lexer

import (
	"fmt"
	"strings"

	"scopelang/internal/diag"
)

type Kind int

const (
	TEOF Kind = iota
	TIdent
	TString
	TInt
	TLBrace // {
	TRBrace // }
	TLParen // (
	TRParen // )
	TEq
	TSemicolon
)

type Token struct {
	Kind Kind
	Val  string
	Line int
	Col  int
	Off  int
}

type Lexer struct {
	src    string
	line   int
	col    int
	off    int
	tokens []Token
}

func Tokenize(src string) ([]Token, error) {
	l := &Lexer{src: src, line: 1, col: 1}
	if err := l.run(); err != nil {
		return nil, err
	}
	return l.tokens, nil
}

func (l *Lexer) run() error {
	for {
		l.skipSpaceAndComments()
		if l.off >= len(l.src) {
			l.push(TEOF, "")
			return nil
		}
		c := l.src[l.off]
		startLine, startCol := l.line, l.col
		switch {
		case c == '{':
			l.advance(); l.pushAt(TLBrace, "{", startLine, startCol)
		case c == '}':
			l.advance(); l.pushAt(TRBrace, "}", startLine, startCol)
		case c == '(':
			l.advance(); l.pushAt(TLParen, "(", startLine, startCol)
		case c == ')':
			l.advance(); l.pushAt(TRParen, ")", startLine, startCol)
		case c == '=':
			l.advance(); l.pushAt(TEq, "=", startLine, startCol)
		case c == ';':
			l.advance(); l.pushAt(TSemicolon, ";", startLine, startCol)
		case c == '"':
			if err := l.readString(startLine, startCol); err != nil {
				return err
			}
		case isDigit(c):
			l.readNumber(startLine, startCol)
		case isIdentStart(c):
			l.readIdent(startLine, startCol)
		default:
			return l.bad(startLine, startCol, fmt.Sprintf("unexpected character %q", c))
		}
	}
}

func (l *Lexer) skipSpaceAndComments() {
	for l.off < len(l.src) {
		c := l.src[l.off]
		if c == ' ' || c == '\t' || c == '\r' || c == '\n' {
			l.advance()
			continue
		}
		if c == '/' && l.off+1 < len(l.src) && l.src[l.off+1] == '/' {
			for l.off < len(l.src) && l.src[l.off] != '\n' {
				l.advance()
			}
			continue
		}
		return
	}
}

func (l *Lexer) readString(startLine, startCol int) error {
	l.advance() // opening quote
	var sb strings.Builder
	for l.off < len(l.src) {
		c := l.src[l.off]
		if c == '"' {
			l.advance()
			l.pushAt(TString, sb.String(), startLine, startCol)
			return nil
		}
		if c == '\n' {
			return l.bad(startLine, startCol, "unterminated string literal")
		}
		if c == '\\' && l.off+1 < len(l.src) {
			esc := l.src[l.off+1]
			l.advance()
			l.advance()
			switch esc {
			case 'n':
				sb.WriteByte('\n')
			case 't':
				sb.WriteByte('\t')
			case '"':
				sb.WriteByte('"')
			case '\\':
				sb.WriteByte('\\')
			default:
				return l.bad(l.line, l.col, fmt.Sprintf("invalid escape \\%c", esc))
			}
			continue
		}
		sb.WriteByte(c)
		l.advance()
	}
	return l.bad(startLine, startCol, "unterminated string literal")
}

func (l *Lexer) readNumber(startLine, startCol int) {
	start := l.off
	for l.off < len(l.src) && isDigit(l.src[l.off]) {
		l.advance()
	}
	l.pushAt(TInt, l.src[start:l.off], startLine, startCol)
}

func (l *Lexer) readIdent(startLine, startCol int) {
	start := l.off
	for l.off < len(l.src) && isIdentPart(l.src[l.off]) {
		l.advance()
	}
	l.pushAt(TIdent, l.src[start:l.off], startLine, startCol)
}

func (l *Lexer) advance() {
	if l.off < len(l.src) {
		if l.src[l.off] == '\n' {
			l.line++
			l.col = 1
		} else {
			l.col++
		}
		l.off++
	}
}

func (l *Lexer) push(k Kind, v string) {
	l.tokens = append(l.tokens, Token{Kind: k, Val: v, Line: l.line, Col: l.col, Off: l.off})
}

func (l *Lexer) pushAt(k Kind, v string, line, col int) {
	l.tokens = append(l.tokens, Token{Kind: k, Val: v, Line: line, Col: col, Off: l.off - len(v)})
}

func (l *Lexer) bad(line, col int, msg string) *diag.Error {
	return diag.New(diag.Input, "LEX", fmt.Sprintf("%d:%d: %s", line, col, msg)).With(diag.PhaseFrontend, "", 0)
}

func isDigit(c byte) bool      { return c >= '0' && c <= '9' }
func isIdentStart(c byte) bool { return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') }
func isIdentPart(c byte) bool  { return isIdentStart(c) || isDigit(c) }
