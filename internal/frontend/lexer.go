package frontend

import "fmt"

type tokenKind int

const (
	tEOF tokenKind = iota
	tIdent
	tInt
	tKeyword
	tOp
)

type token struct {
	kind tokenKind
	val  string
	pos  Pos
}

var keywords = map[string]bool{
	"input": true, "output": true, "int64": true,
	"for": true, "reduce": true, "if": true, "else": true,
	"true": true, "false": true, "len": true,
}

type lexer struct {
	src  string
	i    int
	line int
	col  int
}

func lex(src string) ([]token, error) {
	l := &lexer{src: src, line: 1, col: 1}
	var toks []token
	for {
		l.skipSpaceAndComments()
		if l.i >= len(l.src) {
			toks = append(toks, token{kind: tEOF, pos: Pos{l.line, l.col}})
			return toks, nil
		}
		start := Pos{l.line, l.col}
		c := l.src[l.i]
		switch {
		case c >= '0' && c <= '9':
			var n []byte
			for l.i < len(l.src) && l.src[l.i] >= '0' && l.src[l.i] <= '9' {
				n = append(n, l.src[l.i])
				l.advance()
			}
			toks = append(toks, token{tInt, string(n), start})
		case isIdentStart(c):
			startIdx := l.i
			for l.i < len(l.src) && isIdentPart(l.src[l.i]) {
				l.advance()
			}
			word := l.src[startIdx:l.i]
			k := tIdent
			if keywords[word] {
				k = tKeyword
			}
			toks = append(toks, token{k, word, start})
		default:
			op, ok := l.readOp()
			if !ok {
				return nil, fmt.Errorf("%s: unexpected character %q", start, string(c))
			}
			toks = append(toks, token{tOp, op, start})
		}
	}
}

func (l *lexer) readOp() (string, bool) {
	two := ""
	if l.i+1 < len(l.src) {
		two = l.src[l.i : l.i+2]
	}
	switch two {
	case ":=", "..", "==", "!=", "<=", ">=", "&&", "||":
		l.advance()
		l.advance()
		return two, true
	}
	switch l.src[l.i] {
	case '+', '-', '*', '/', '%', '&', '|', '^', '!', '~',
		'<', '>', '=', '(', ')', '{', '}', '[', ']', ';', ',':
		op := string(l.src[l.i])
		l.advance()
		return op, true
	}
	return "", false
}

func (l *lexer) skipSpaceAndComments() {
	for l.i < len(l.src) {
		c := l.src[l.i]
		if c == ' ' || c == '\t' || c == '\r' || c == '\n' {
			l.advance()
			continue
		}
		if c == '/' && l.i+1 < len(l.src) && l.src[l.i+1] == '/' {
			for l.i < len(l.src) && l.src[l.i] != '\n' {
				l.advance()
			}
			continue
		}
		return
	}
}

func (l *lexer) advance() {
	if l.i < len(l.src) && l.src[l.i] == '\n' {
		l.line++
		l.col = 1
	} else {
		l.col++
	}
	l.i++
}

func isIdentStart(c byte) bool {
	return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
}

func isIdentPart(c byte) bool {
	return isIdentStart(c) || (c >= '0' && c <= '9')
}
