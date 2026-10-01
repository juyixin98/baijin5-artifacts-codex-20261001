package frontend

import (
	"strings"

	"genstatemachine/internal/gerr"
)

// Lexer turns source text into tokens.
type Lexer struct {
	src  []rune
	pos  int
	line int
	col  int
	toks []Token
}

// Lex tokenizes src.
func Lex(src string) ([]Token, error) {
	l := &Lexer{src: []rune(src), line: 1, col: 0}
	if err := l.run(); err != nil {
		return nil, err
	}
	return l.toks, nil
}

func (l *Lexer) peek() rune {
	if l.pos >= len(l.src) {
		return 0
	}
	return l.src[l.pos]
}

func (l *Lexer) peekAt(n int) rune {
	if l.pos+n >= len(l.src) {
		return 0
	}
	return l.src[l.pos+n]
}

func (l *Lexer) advance() rune {
	ch := l.src[l.pos]
	l.pos++
	if ch == '\n' {
		l.line++
		l.col = 0
	} else {
		l.col++
	}
	return ch
}

func (l *Lexer) run() error {
	for {
		l.skipSpace()
		if l.peek() == 0 {
			l.toks = append(l.toks, Token{Kind: TEOF, Line: l.line, Col: l.col})
			return nil
		}
		startLine, startCol := l.line, l.col
		ch := l.peek()
		switch {
		case isIdentStart(ch):
			if err := l.ident(startLine, startCol); err != nil {
				return err
			}
		case isDigit(ch):
			l.number(startLine, startCol)
		case ch == '"':
			if err := l.str(startLine, startCol); err != nil {
				return err
			}
		default:
			if err := l.operator(startLine, startCol); err != nil {
				return err
			}
		}
	}
}

func (l *Lexer) skipSpace() {
	for l.peek() != 0 {
		ch := l.peek()
		if ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' {
			l.advance()
			continue
		}
		// // line comment or /* block comment */
		if ch == '/' && l.peekAt(1) == '/' {
			for l.peek() != 0 && l.peek() != '\n' {
				l.advance()
			}
			continue
		}
		if ch == '/' && l.peekAt(1) == '*' {
			l.advance()
			l.advance()
			for l.peek() != 0 && !(l.peek() == '*' && l.peekAt(1) == '/') {
				l.advance()
			}
			if l.peek() == 0 {
				return
			}
			l.advance()
			l.advance()
			continue
		}
		return
	}
}

func isIdentStart(ch rune) bool {
	return ch == '_' || (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
}

func isIdentPart(ch rune) bool {
	return isIdentStart(ch) || isDigit(ch)
}

func isDigit(ch rune) bool { return ch >= '0' && ch <= '9' }

func (l *Lexer) ident(line, col int) error {
	var b strings.Builder
	for isIdentPart(l.peek()) {
		b.WriteRune(l.advance())
	}
	val := b.String()
	kind := TIdent
	if kw, ok := keywords[val]; ok {
		kind = kw
	}
	l.toks = append(l.toks, Token{Kind: kind, Val: val, Line: line, Col: col})
	return nil
}

func (l *Lexer) number(line, col int) {
	var b strings.Builder
	for isDigit(l.peek()) {
		b.WriteRune(l.advance())
	}
	l.toks = append(l.toks, Token{Kind: TInt, Val: b.String(), Line: line, Col: col})
}

func (l *Lexer) str(line, col int) error {
	l.advance() // opening quote
	var b strings.Builder
	for l.peek() != 0 && l.peek() != '"' {
		ch := l.advance()
		if ch == '\\' {
			if l.peek() == 0 {
				break
			}
			esc := l.advance()
			switch esc {
			case 'n':
				b.WriteRune('\n')
			case 't':
				b.WriteRune('\t')
			case 'r':
				b.WriteRune('\r')
			case '"':
				b.WriteRune('"')
			case '\\':
				b.WriteRune('\\')
			default:
				return gerr.New(gerr.EParse, "invalid escape sequence \\%c", esc).AtPos(l.line, l.col)
			}
			continue
		}
		b.WriteRune(ch)
	}
	if l.peek() != '"' {
		return gerr.New(gerr.EParse, "unterminated string literal").AtPos(line, col)
	}
	l.advance() // closing quote
	l.toks = append(l.toks, Token{Kind: TStr, Val: b.String(), Line: line, Col: col})
	return nil
}

func (l *Lexer) emit(k TokenKind, v string, line, col int) {
	l.toks = append(l.toks, Token{Kind: k, Val: v, Line: line, Col: col})
}

func (l *Lexer) operator(line, col int) error {
	ch := l.advance()
	switch ch {
	case '(':
		l.emit(TLParen, "(", line, col)
	case ')':
		l.emit(TRParen, ")", line, col)
	case '{':
		l.emit(TLBrace, "{", line, col)
	case '}':
		l.emit(TRBrace, "}", line, col)
	case ',':
		l.emit(TComma, ",", line, col)
	case ';':
		l.emit(TSemicol, ";", line, col)
	case '+':
		l.emit(TPlus, "+", line, col)
	case '-':
		l.emit(TMINUS, "-", line, col)
	case '*':
		l.emit(TStar, "*", line, col)
	case '/':
		l.emit(TSlash, "/", line, col)
	case '%':
		l.emit(TPct, "%", line, col)
	case '=':
		if l.peek() == '=' {
			l.advance()
			l.emit(TEq, "==", line, col)
		} else {
			l.emit(TAssign, "=", line, col)
		}
	case '!':
		if l.peek() == '=' {
			l.advance()
			l.emit(TNe, "!=", line, col)
		} else {
			l.emit(TBang, "!", line, col)
		}
	case '<':
		if l.peek() == '=' {
			l.advance()
			l.emit(TLe, "<=", line, col)
		} else {
			l.emit(TLt, "<", line, col)
		}
	case '>':
		if l.peek() == '=' {
			l.advance()
			l.emit(TGe, ">=", line, col)
		} else {
			l.emit(TGt, ">", line, col)
		}
	case '&':
		if l.peek() == '&' {
			l.advance()
			l.emit(TAnd, "&&", line, col)
		} else {
			return gerr.New(gerr.EParse, "unexpected character '&'").AtPos(line, col)
		}
	case '|':
		if l.peek() == '|' {
			l.advance()
			l.emit(TOr, "||", line, col)
		} else {
			return gerr.New(gerr.EParse, "unexpected character '|'").AtPos(line, col)
		}
	default:
		return gerr.New(gerr.EParse, "unexpected character %q", string(ch)).AtPos(line, col)
	}
	return nil
}
