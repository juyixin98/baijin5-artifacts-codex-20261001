package front

import (
	"fmt"

	"funcspect/internal/diag"
)

// TokenKind enumerates lexical token classes.
type TokenKind int

const (
	TEOF TokenKind = iota
	TIdent
	TInt
	TKeyword
	TOp
	TLParen
	TRParen
)

type Token struct {
	Kind  TokenKind
	Value string
	Off   int
	Line  int
	Col   int
}

type lexer struct {
	src    string
	pos    int
	line   int
	col    int
	tokens []Token
}

var keywords = map[string]bool{
	"pure": true, "impure": true,
	"let": true, "in": true,
	"if": true, "then": true, "else": true,
	"true": true, "false": true,
}

func lex(src string) ([]Token, error) {
	l := &lexer{src: src, line: 1, col: 1}
	for l.pos < len(l.src) {
		c := l.src[l.pos]
		switch {
		case c == ' ' || c == '\t' || c == '\r':
			l.advance()
		case c == '\n':
			l.advance()
		case c == '-' && l.peek(1) == '-':
			l.skipLineComment()
		case c == '(':
			l.emit(TLParen, "(")
		case c == ')':
			l.emit(TRParen, ")")
		case isDigit(c):
			l.readNumber()
		case isIdentStart(c):
			l.readIdent()
		default:
			if op := l.peekOperator(); op != "" {
				l.emit(TOp, op)
				continue
			}
			tok := Token{Off: l.pos, Line: l.line, Col: l.col}
			return nil, diag.New(diag.CatSyntax, fmt.Sprintf("unexpected character %q", string(c))).At(tok.Line, tok.Col)
		}
	}
	l.tokens = append(l.tokens, Token{Kind: TEOF, Off: len(l.src), Line: l.line, Col: l.col})
	return l.tokens, nil
}

func (l *lexer) skipLineComment() {
	for l.pos < len(l.src) && l.src[l.pos] != '\n' {
		l.advance()
	}
}

// peekOperator recognizes the longest operator at the current position without
// consuming it; emit() performs the single authoritative advance.
func (l *lexer) peekOperator() string {
	if isOp3(l.slice(3)) {
		return l.slice(3)
	}
	if isOp2(l.slice(2)) {
		return l.slice(2)
	}
	if l.pos < len(l.src) && isOp1(rune(l.src[l.pos])) {
		return l.src[l.pos : l.pos+1]
	}
	return ""
}

func isOp1(r rune) bool {
	switch r {
	case '+', '-', '*', '/', '%', '<', '>', '=', '!':
		return true
	}
	return false
}

func isOp2(s string) bool {
	switch s {
	case "==", "!=", "<=", ">=", "&&", "||":
		return true
	}
	return false
}

func isOp3(s string) bool { return false }

func (l *lexer) readNumber() {
	n := 0
	for l.pos+n < len(l.src) && isDigit(l.src[l.pos+n]) {
		n++
	}
	val := l.src[l.pos : l.pos+n]
	l.emitN(TInt, val, n)
}

func (l *lexer) readIdent() {
	n := 0
	for l.pos+n < len(l.src) && isIdentPart(l.src[l.pos+n]) {
		n++
	}
	val := l.src[l.pos : l.pos+n]
	kind := TIdent
	if keywords[val] {
		kind = TKeyword
	}
	l.emitN(kind, val, n)
}

func (l *lexer) emitN(kind TokenKind, val string, n int) {
	off, line, col := l.pos, l.line, l.col
	for i := 0; i < n; i++ {
		l.advance()
	}
	l.tokens = append(l.tokens, Token{Kind: kind, Value: val, Off: off, Line: line, Col: col})
}

func (l *lexer) emit(kind TokenKind, val string) { l.emitN(kind, val, len(val)) }

func (l *lexer) advance() {
	if l.pos < len(l.src) {
		if l.src[l.pos] == '\n' {
			l.line++
			l.col = 1
		} else {
			l.col++
		}
		l.pos++
	}
}

func (l *lexer) peek(n int) byte {
	if l.pos+n < len(l.src) {
		return l.src[l.pos+n]
	}
	return 0
}

func (l *lexer) slice(n int) string {
	if l.pos+n <= len(l.src) {
		return l.src[l.pos : l.pos+n]
	}
	return ""
}

func isDigit(c byte) bool      { return c >= '0' && c <= '9' }
func isIdentStart(c byte) bool { return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') }
func isIdentPart(c byte) bool  { return isIdentStart(c) || isDigit(c) }
