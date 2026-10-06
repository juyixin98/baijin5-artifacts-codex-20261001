package frontend

import (
	"strings"
)

type tokKind int

const (
	tEOF tokKind = iota
	tIdent
	tInt
	tString
	tLParen
	tRParen
	tLBrace
	tRBrace
	tComma
	tPipe
	tArrow
	tWildcard
)

type token struct {
	kind tokKind
	text string
	pos  Pos
}

func (t token) describe() string {
	switch t.kind {
	case tEOF:
		return "end of input"
	case tIdent:
		return "identifier " + strconv_Quote(t.text)
	case tInt:
		return "integer " + strconv_Quote(t.text)
	case tString:
		return "string " + strconv_Quote(t.text)
	case tLParen:
		return "'('"
	case tRParen:
		return "')'"
	case tLBrace:
		return "'{'"
	case tRBrace:
		return "'}'"
	case tComma:
		return "','"
	case tPipe:
		return "'|'"
	case tArrow:
		return "'=>'"
	case tWildcard:
		return "'_'"
	}
	return "?"
}

func strconv_Quote(s string) string {
	return "\"" + s + "\""
}

func isIdentStart(c byte) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z'
}

func isIdentChar(c byte) bool {
	return isIdentStart(c) || c >= '0' && c <= '9' || c == '\''
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func lex(src string) ([]token, *Error) {
	var toks []token
	line, col := 1, 1
	i := 0
	for i < len(src) {
		c := src[i]
		pos := Pos{Line: line, Col: col}
		switch {
		case c == '\n':
			i++
			line++
			col = 1
		case c == ' ' || c == '\t' || c == '\r':
			i++
			col++
		case c == '#':
			for i < len(src) && src[i] != '\n' {
				i++
			}
		case isIdentStart(c):
			start := i
			for i < len(src) && isIdentChar(src[i]) {
				i++
				col++
			}
			toks = append(toks, token{tIdent, src[start:i], pos})
		case isDigit(c) || c == '-' && i+1 < len(src) && isDigit(src[i+1]):
			start := i
			if c == '-' {
				i++
				col++
			}
			for i < len(src) && isDigit(src[i]) {
				i++
				col++
			}
			toks = append(toks, token{tInt, src[start:i], pos})
		case c == '"':
			i++
			col++
			var sb strings.Builder
			closed := false
			for i < len(src) {
				ch := src[i]
				if ch == '"' {
					i++
					col++
					closed = true
					break
				}
				if ch == '\n' {
					return nil, errorf(CatParse, pos, "unterminated string literal")
				}
				if ch == '\\' {
					if i+1 >= len(src) {
						return nil, errorf(CatParse, pos, "unterminated string literal")
					}
					switch esc := src[i+1]; esc {
					case 'n':
						sb.WriteByte('\n')
					case 't':
						sb.WriteByte('\t')
					case '"':
						sb.WriteByte('"')
					case '\\':
						sb.WriteByte('\\')
					default:
						return nil, errorf(CatParse, Pos{Line: line, Col: col}, "unknown escape sequence \\%c", esc)
					}
					i += 2
					col += 2
					continue
				}
				sb.WriteByte(ch)
				i++
				col++
			}
			if !closed {
				return nil, errorf(CatParse, pos, "unterminated string literal")
			}
			toks = append(toks, token{tString, sb.String(), pos})
		case c == '_':
			if i+1 < len(src) && isIdentChar(src[i+1]) {
				return nil, errorf(CatParse, pos, "identifiers may not start with '_'")
			}
			toks = append(toks, token{tWildcard, "_", pos})
			i++
			col++
		case c == '(':
			toks = append(toks, token{tLParen, "(", pos})
			i++
			col++
		case c == ')':
			toks = append(toks, token{tRParen, ")", pos})
			i++
			col++
		case c == '{':
			toks = append(toks, token{tLBrace, "{", pos})
			i++
			col++
		case c == '}':
			toks = append(toks, token{tRBrace, "}", pos})
			i++
			col++
		case c == ',':
			toks = append(toks, token{tComma, ",", pos})
			i++
			col++
		case c == '|':
			toks = append(toks, token{tPipe, "|", pos})
			i++
			col++
		case c == '=':
			if i+1 < len(src) && src[i+1] == '>' {
				toks = append(toks, token{tArrow, "=>", pos})
				i += 2
				col += 2
			} else {
				return nil, errorf(CatParse, pos, "unexpected '=', did you mean '=>'?")
			}
		default:
			return nil, errorf(CatParse, pos, "unexpected character %q", rune(c))
		}
	}
	toks = append(toks, token{tEOF, "", Pos{Line: line, Col: col}})
	return toks, nil
}
