package frontend

import "fmt"

func Lex(src string) ([]Token, error) {
	lx := &lexer{src: src}
	return lx.run()
}

type lexer struct {
	src  string
	pos  int
	line int
	col  int
}

func (l *lexer) run() ([]Token, error) {
	toks := make([]Token, 0, 64)
	for {
		l.skipSpace()
		if l.pos >= len(l.src) {
			toks = append(toks, Token{Kind: TEOF, Pos: l.here()})
			return toks, nil
		}
		start := l.here()
		c := l.src[l.pos]
		switch {
		case c == '/' && l.peek(1) == '/':
			for l.pos < len(l.src) && l.src[l.pos] != '\n' {
				l.adv()
			}
		case isDigit(c):
			s := l.takeWhile(isDigit)
			toks = append(toks, Token{Kind: TInt, Value: s, Pos: start})
		case isIdentStart(c):
			s := l.takeWhile(isIdentPart)
			kind := TIdent
			if k, ok := keywords[s]; ok {
				kind = k
			}
			toks = append(toks, Token{Kind: kind, Value: s, Pos: start})
		case c == '"':
			s, err := l.lexString()
			if err != nil {
				return nil, err
			}
			toks = append(toks, Token{Kind: TStr, Value: s, Pos: start})
		default:
			t, err := l.lexOp()
			if err != nil {
				return nil, err
			}
			t.Pos = start
			toks = append(toks, t)
		}
	}
}

func (l *lexer) lexString() (string, error) {
	l.adv()
	var out []byte
	for l.pos < len(l.src) {
		c := l.src[l.pos]
		if c == '"' {
			l.adv()
			return string(out), nil
		}
		if c == '\\' {
			l.adv()
			if l.pos >= len(l.src) {
				break
			}
			e := l.src[l.pos]
			l.adv()
			switch e {
			case 'n':
				out = append(out, '\n')
			case 't':
				out = append(out, '\t')
			case '"':
				out = append(out, '"')
			case '\\':
				out = append(out, '\\')
			default:
				return "", fmt.Errorf("line %d: invalid escape \\%c", l.line, e)
			}
			continue
		}
		out = append(out, c)
		l.adv()
	}
	return "", fmt.Errorf("line %d: unterminated string", l.line)
}

func (l *lexer) lexOp() (Token, error) {
	c := l.src[l.pos]
	two := ""
	if l.pos+1 < len(l.src) {
		two = l.src[l.pos : l.pos+2]
	}
	switch two {
	case "::":
		l.adv()
		l.adv()
		return Token{Kind: TColonColon}, nil
	case "->":
		l.adv()
		l.adv()
		return Token{Kind: TArrow}, nil
	case "==":
		l.adv()
		l.adv()
		return Token{Kind: TEq}, nil
	case "!=":
		l.adv()
		l.adv()
		return Token{Kind: TNotEq}, nil
	case "<=":
		l.adv()
		l.adv()
		return Token{Kind: TLe}, nil
	case ">=":
		l.adv()
		l.adv()
		return Token{Kind: TGe}, nil
	}
	l.adv()
	switch c {
	case '(':
		return Token{Kind: TLParen}, nil
	case ')':
		return Token{Kind: TRParen}, nil
	case '{':
		return Token{Kind: LBrace}, nil
	case '}':
		return Token{Kind: RBrace}, nil
	case ',':
		return Token{Kind: TComma}, nil
	case ':':
		return Token{Kind: TColon}, nil
	case ';':
		return Token{Kind: TSemi}, nil
	case '+':
		return Token{Kind: TPlus}, nil
	case '-':
		return Token{Kind: TMinus}, nil
	case '*':
		return Token{Kind: TStar}, nil
	case '/':
		return Token{Kind: TSlash}, nil
	case '=':
		return Token{Kind: TAssign}, nil
	case '<':
		return Token{Kind: TLt}, nil
	case '>':
		return Token{Kind: TGt}, nil
	case '%':
		return Token{Kind: TMod}, nil
	}
	return Token{}, fmt.Errorf("line %d col %d: unexpected character %q", l.line, l.col, string(c))
}

func (l *lexer) takeWhile(pred func(byte) bool) string {
	start := l.pos
	for l.pos < len(l.src) && pred(l.src[l.pos]) {
		l.adv()
	}
	return l.src[start:l.pos]
}

func (l *lexer) skipSpace() {
	for l.pos < len(l.src) {
		c := l.src[l.pos]
		if c == ' ' || c == '\t' || c == '\r' || c == '\n' {
			l.adv()
			continue
		}
		return
	}
}

func (l *lexer) adv() {
	if l.pos < len(l.src) {
		if l.src[l.pos] == '\n' {
			l.line++
			l.col = 0
		}
		l.pos++
		l.col++
	}
}

func (l *lexer) peek(n int) byte {
	if l.pos+n < len(l.src) {
		return l.src[l.pos+n]
	}
	return 0
}

func (l *lexer) here() Position {
	if l.line == 0 {
		return Position{Line: 1, Col: 1}
	}
	return Position{Line: l.line, Col: l.col, Off: l.pos}
}

func isDigit(c byte) bool      { return c >= '0' && c <= '9' }
func isIdentStart(c byte) bool { return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') }
func isIdentPart(c byte) bool  { return isIdentStart(c) || isDigit(c) }
