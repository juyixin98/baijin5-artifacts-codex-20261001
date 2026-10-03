package frontend

import "fmt"

// LexError is a lexical error with a source position.
type LexError struct {
	Line int
	Col  int
	Msg  string
}

func (e *LexError) Error() string {
	return fmt.Sprintf("lex error at line %d col %d: %s", e.Line, e.Col, e.Msg)
}

func lex(src string) ([]Token, error) {
	runes := []rune(src)
	var toks []Token
	i := 0
	line, col := 1, 1

	add := func(k Kind, lit string, l, c int) {
		toks = append(toks, Token{Kind: k, Lit: lit, Line: l, Col: c})
	}
	at := func() rune { return runes[i] }
	advance := func() {
		if runes[i] == '\n' {
			line++
			col = 1
		} else {
			col++
		}
		i++
	}

	for i < len(runes) {
		c := at()
		switch {
		case c == ' ' || c == '\t' || c == '\r' || c == '\n':
			advance()
		case c == '/' && i+1 < len(runes) && runes[i+1] == '/':
			for i < len(runes) && at() != '\n' {
				advance()
			}
		case c == '/' && i+1 < len(runes) && runes[i+1] == '*':
			startLine, startCol := line, col
			advance()
			advance()
			closed := false
			for i < len(runes) {
				if at() == '*' && i+1 < len(runes) && runes[i+1] == '/' {
					advance()
					advance()
					closed = true
					break
				}
				advance()
			}
			if !closed {
				return nil, &LexError{Line: startLine, Col: startCol, Msg: "unterminated block comment"}
			}
		case isLetter(c) || c == '_':
			l, cc := line, col
			start := i
			for i < len(runes) && (isLetter(at()) || isDigit(at()) || at() == '_') {
				advance()
			}
			lit := string(runes[start:i])
			if k, ok := keywords[lit]; ok {
				add(k, lit, l, cc)
			} else {
				add(TIdent, lit, l, cc)
			}
		case isDigit(c):
			l, cc := line, col
			start := i
			for i < len(runes) && isDigit(at()) {
				advance()
			}
			add(TInt, string(runes[start:i]), l, cc)
		case c == '"':
			l, cc := line, col
			advance() // opening quote
			var val []rune
			closed := false
			for i < len(runes) && at() != '"' {
				if at() == '\n' {
					return nil, &LexError{Line: l, Col: cc, Msg: "newline in string literal"}
				}
				if at() == '\\' {
					advance()
					if i >= len(runes) {
						return nil, &LexError{Line: line, Col: col, Msg: "dangling escape"}
					}
					esc := at()
					advance()
					switch esc {
					case 'n':
						val = append(val, '\n')
					case 't':
						val = append(val, '\t')
					case 'r':
						val = append(val, '\r')
					case '"':
						val = append(val, '"')
					case '\\':
						val = append(val, '\\')
					default:
						return nil, &LexError{Line: line, Col: col, Msg: fmt.Sprintf("unknown escape \\%c", esc)}
					}
				} else {
					val = append(val, at())
					advance()
				}
			}
			if i >= len(runes) {
				return nil, &LexError{Line: l, Col: cc, Msg: "unterminated string literal"}
			}
			advance() // closing quote
			add(TStr, string(val), l, cc)
			closed = true
			_ = closed
		default:
			l, cc := line, col
			switch c {
			case '(':
				add(TLParen, "(", l, cc)
			case ')':
				add(TRParen, ")", l, cc)
			case '{':
				add(TLBrace, "{", l, cc)
			case '}':
				add(TRBrace, "}", l, cc)
			case '[':
				add(TLBrack, "[", l, cc)
			case ']':
				add(TRBrack, "]", l, cc)
			case ',':
				add(TComma, ",", l, cc)
			case ':':
				advance()
				if i < len(runes) && at() == ':' {
					add(TQual, "::", l, cc)
					advance()
					continue
				}
				add(TColon, ":", l, cc)
				continue
			case '+':
				add(TPlus, "+", l, cc)
			case '-':
				add(TMinus, "-", l, cc)
			case '*':
				add(TStar, "*", l, cc)
			case '/':
				add(TSlash, "/", l, cc)
			case '=':
				advance()
				if i < len(runes) && at() == '=' {
					add(TEq, "==", l, cc)
					advance()
					continue
				}
				add(TAssign, "=", l, cc)
				continue
			case '!':
				advance()
				if i < len(runes) && at() == '=' {
					add(TNeq, "!=", l, cc)
					advance()
					continue
				}
				return nil, &LexError{Line: l, Col: cc, Msg: `unexpected '!'`}
			case '<':
				advance()
				if i < len(runes) && at() == '=' {
					add(TLe, "<=", l, cc)
					advance()
					continue
				}
				add(TLt, "<", l, cc)
				continue
			case '>':
				advance()
				if i < len(runes) && at() == '=' {
					add(TGe, ">=", l, cc)
					advance()
					continue
				}
				add(TGt, ">", l, cc)
				continue
			default:
				return nil, &LexError{Line: l, Col: cc, Msg: fmt.Sprintf("unexpected character %q", c)}
			}
			advance()
		}
	}
	add(TEOF, "", line, col)
	return toks, nil
}

func isLetter(r rune) bool {
	return r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z'
}

func isDigit(r rune) bool {
	return r >= '0' && r <= '9'
}
