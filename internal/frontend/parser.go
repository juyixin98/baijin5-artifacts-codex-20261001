package frontend

import (
	"fmt"
	"strconv"
)

func Parse(name, src string) (*Module, error) {
	toks, err := Lex(src)
	if err != nil {
		return nil, err
	}
	p := &parser{name: name, toks: toks}
	mod := &Module{Name: name}
	if p.peek().Kind == kwModule {
		p.next()
		t := p.next()
		if t.Kind != TIdent {
			return nil, p.ferr(t.Pos, "expected module name")
		}
		mod.Name = t.Value
		p.expectSemi()
	}
	for p.peek().Kind == kwImport {
		p.next()
		t := p.next()
		if t.Kind != TIdent {
			return nil, p.ferr(t.Pos, "expected module name after import")
		}
		mod.Imports = append(mod.Imports, t.Value)
		if err := p.expectSemi(); err != nil {
			return nil, err
		}
	}
	for p.peek().Kind != TEOF {
		d, err := p.parseDecl()
		if err != nil {
			return nil, err
		}
		mod.Decls = append(mod.Decls, d)
	}
	return mod, nil
}

type parser struct {
	name string
	toks []Token
	idx  int
}

func (p *parser) parseDecl() (Decl, error) {
	pub := false
	generic := false
	pos := p.peek().Pos
	if p.peek().Kind == kwPub {
		pub = true
		p.next()
	}
	if p.peek().Kind == kwGeneric {
		generic = true
		p.next()
		if !pub {
			return nil, p.ferr(p.peek().Pos, "generic must be combined with pub fn (generic fn %s)", p.peek().Value)
		}
	}
	switch p.peek().Kind {
	case kwConst:
		if generic {
			return nil, p.ferr(pos, "const cannot be generic")
		}
		return p.parseConst(pub, pos)
	case kwFn:
		return p.parseFn(pub, generic, pos)
	default:
		return nil, p.ferr(p.peek().Pos, "expected fn or const declaration, got %q", p.peek().Value)
	}
}

func (p *parser) parseConst(pub bool, pos Position) (Decl, error) {
	p.next() // const
	name := p.next()
	if name.Kind != TIdent {
		return nil, p.ferr(name.Pos, "expected constant name")
	}
	if p.peek().Kind != TColon {
		return nil, p.ferr(p.peek().Pos, "expected ':' after const %s", name.Value)
	}
	p.next()
	ty := p.next()
	if !isTypeTok(ty.Kind) {
		return nil, p.ferr(ty.Pos, "expected type after const %s:", name.Value)
	}
	if p.peek().Kind != TAssign {
		return nil, p.ferr(p.peek().Pos, "expected '=' in const %s", name.Value)
	}
	p.next()
	v, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if err := p.expectSemi(); err != nil {
		return nil, err
	}
	return &ConstDecl{Pub: pub, Name: name.Value, Type: tokType(ty.Kind), Value: v, Inline: true, Pos: pos}, nil
}

func (p *parser) parseFn(pub, generic bool, pos Position) (Decl, error) {
	p.next() // fn
	name := p.next()
	if name.Kind != TIdent {
		return nil, p.ferr(name.Pos, "expected function name")
	}
	if p.peek().Kind != TLParen {
		return nil, p.ferr(p.peek().Pos, "expected '(' after fn %s", name.Value)
	}
	p.next()
	params := []Param{}
	for p.peek().Kind != TRParen {
		pn := p.next()
		if pn.Kind != TIdent {
			return nil, p.ferr(pn.Pos, "expected parameter name")
		}
		if p.peek().Kind != TColon {
			return nil, p.ferr(p.peek().Pos, "expected ':' after parameter %s", pn.Value)
		}
		p.next()
		ty := p.next()
		if !isTypeTok(ty.Kind) && ty.Kind != TIdent {
			return nil, p.ferr(ty.Pos, "expected parameter type")
		}
		params = append(params, Param{Name: pn.Value, Type: typeText(ty)})
		if p.peek().Kind == TComma {
			p.next()
			continue
		}
		break
	}
	if p.next().Kind != TRParen {
		return nil, p.ferr(p.peek().Pos, "expected ')'")
	}
	result := ""
	if p.peek().Kind == TArrow {
		p.next()
		ty := p.next()
		if !isTypeTok(ty.Kind) && ty.Kind != TIdent {
			return nil, p.ferr(ty.Pos, "expected result type")
		}
		result = typeText(ty)
	}
	if p.peek().Kind != LBrace {
		return nil, p.ferr(p.peek().Pos, "expected function body")
	}
	p.next()
	body, err := p.parseStmtList(RBrace)
	if err != nil {
		return nil, err
	}
	return &FnDecl{Pub: pub, Name: name.Value, Generic: generic, Params: params, Result: result, Body: body, Pos: pos}, nil
}

func (p *parser) parseStmtList(end TokenKind) ([]Stmt, error) {
	stmts := []Stmt{}
	for p.peek().Kind != end && p.peek().Kind != TEOF {
		s, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		stmts = append(stmts, s)
	}
	if p.next().Kind != end {
		return nil, p.ferr(p.peek().Pos, "expected end of block")
	}
	return stmts, nil
}

func (p *parser) parseStmt() (Stmt, error) {
	pos := p.peek().Pos
	switch p.peek().Kind {
	case kwLet:
		p.next()
		n := p.next()
		if n.Kind != TIdent {
			return nil, p.ferr(n.Pos, "expected binding name")
		}
		ty := ""
		if p.peek().Kind == TColon {
			p.next()
			tt := p.next()
			if !isTypeTok(tt.Kind) && tt.Kind != TIdent {
				return nil, p.ferr(tt.Pos, "expected type")
			}
			ty = typeText(tt)
		}
		if p.peek().Kind != TAssign {
			return nil, p.ferr(p.peek().Pos, "expected '=' in let")
		}
		p.next()
		v, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if err := p.optSemi(); err != nil {
			return nil, err
		}
		return &LetStmt{Name: n.Value, Type: ty, Value: v, Pos: pos}, nil
	case kwReturn:
		p.next()
		var v Expr
		has := false
		if p.peek().Kind != TSemi && p.peek().Kind != RBrace {
			e, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			v = e
			has = true
		}
		if err := p.optSemi(); err != nil {
			return nil, err
		}
		return &ReturnStmt{Value: v, Has: has, Pos: pos}, nil
	case kwIf:
		p.next()
		if p.peek().Kind != TLParen {
			return nil, p.ferr(p.peek().Pos, "expected '(' after if")
		}
		p.next()
		cond, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if p.next().Kind != TRParen {
			return nil, p.ferr(p.peek().Pos, "expected ')' after if condition")
		}
		if p.peek().Kind != LBrace {
			return nil, p.ferr(p.peek().Pos, "expected '{' after if")
		}
		p.next()
		then, err := p.parseStmtList(RBrace)
		if err != nil {
			return nil, err
		}
		var els []Stmt
		if p.peek().Kind == kwElse {
			p.next()
			if p.peek().Kind != LBrace {
				return nil, p.ferr(p.peek().Pos, "expected '{' after else")
			}
			p.next()
			els, err = p.parseStmtList(RBrace)
			if err != nil {
				return nil, err
			}
		}
		return &IfStmt{Cond: cond, Then: then, Else: els, Pos: pos}, nil
	default:
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if err := p.optSemi(); err != nil {
			return nil, err
		}
		return &ExprStmt{Expr: e}, nil
	}
}

// Pratt-ish expression parser with explicit precedence levels.
func (p *parser) parseExpr() (Expr, error) { return p.parseBinary(1) }

func (p *parser) parseBinary(minPrec int) (Expr, error) {
	lhs, err := p.parseUnary()
	if err != nil {
		return nil, err
	}
	for {
		t := p.peek()
		prec := binPrec(t.Kind)
		if prec < minPrec {
			return lhs, nil
		}
		p.next()
		rhs, err := p.parseBinary(prec + 1)
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Op: opName(t.Kind), Lhs: lhs, Rhs: rhs}
	}
}

func (p *parser) parseUnary() (Expr, error) {
	if p.peek().Kind == TMinus {
		p.next()
		e, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &UnaryExpr{Op: "-", Inner: e}, nil
	}
	return p.parsePrimary()
}

func (p *parser) parsePrimary() (Expr, error) {
	t := p.peek()
	switch t.Kind {
	case TInt:
		p.next()
		n, err := strconv.ParseInt(t.Value, 10, 64)
		if err != nil {
			return nil, p.ferr(t.Pos, "bad integer %q", t.Value)
		}
		return &IntLit{Value: n}, nil
	case TStr:
		p.next()
		return &StrLit{Value: t.Value}, nil
	case kwTrue:
		p.next()
		return &BoolLit{Value: true}, nil
	case kwFalse:
		p.next()
		return &BoolLit{Value: false}, nil
	case TLParen:
		p.next()
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if p.next().Kind != TRParen {
			return nil, p.ferr(p.peek().Pos, "expected ')'")
		}
		return e, nil
	case TIdent:
		p.next()
		if p.peek().Kind == TColonColon {
			p.next()
			mem := p.next()
			if mem.Kind != TIdent {
				return nil, p.ferr(mem.Pos, "expected member after '::'")
			}
			if p.peek().Kind != TLParen {
				return &IdentExpr{Name: t.Value + "::" + mem.Value}, nil
			}
			p.next()
			args, err := p.parseCallArgs()
			if err != nil {
				return nil, err
			}
			return &CallExpr{Name: t.Value + "::" + mem.Value, Args: args}, nil
		}
		if p.peek().Kind == TLParen {
			p.next()
			args, err := p.parseCallArgs()
			if err != nil {
				return nil, err
			}
			return &CallExpr{Name: t.Value, Args: args}, nil
		}
		return &IdentExpr{Name: t.Value}, nil
	default:
		return nil, p.ferr(t.Pos, "unexpected token %q", t.Value)
	}
}

func (p *parser) parseCallArgs() ([]Expr, error) {
	args := []Expr{}
	for p.peek().Kind != TRParen {
		a, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		args = append(args, a)
		if p.peek().Kind == TComma {
			p.next()
			continue
		}
		break
	}
	if p.next().Kind != TRParen {
		return nil, p.ferr(p.peek().Pos, "expected ')' in call")
	}
	return args, nil
}

func (p *parser) peek() Token {
	if p.idx < len(p.toks) {
		return p.toks[p.idx]
	}
	return Token{Kind: TEOF}
}

func (p *parser) next() Token {
	t := p.peek()
	if p.idx < len(p.toks) {
		p.idx++
	}
	return t
}

func (p *parser) expectSemi() error {
	if t := p.next(); t.Kind != TSemi {
		return p.ferr(t.Pos, "expected ';'")
	}
	return nil
}

func (p *parser) optSemi() error {
	if p.peek().Kind == TSemi {
		p.next()
	}
	return nil
}

func (p *parser) ferr(pos Position, format string, args ...any) error {
	return fmt.Errorf("%s:%d:%d: %s", p.name, pos.Line, pos.Col, fmt.Sprintf(format, args...))
}

func isTypeTok(k TokenKind) bool {
	return k == kwIntTy || k == kwStrTy || k == kwBoolTy
}

func tokType(k TokenKind) string {
	switch k {
	case kwIntTy:
		return "int"
	case kwStrTy:
		return "string"
	case kwBoolTy:
		return "bool"
	}
	return ""
}

func typeText(t Token) string {
	if t.Kind == TIdent {
		return t.Value
	}
	return tokType(t.Kind)
}

func binPrec(k TokenKind) int {
	switch k {
	case TStar, TSlash, TMod:
		return 4
	case TPlus, TMinus:
		return 3
	case TLt, TLe, TGt, TGe:
		return 2
	case TEq, TNotEq:
		return 1
	}
	return 0
}

func opName(k TokenKind) string {
	switch k {
	case TPlus:
		return "+"
	case TMinus:
		return "-"
	case TStar:
		return "*"
	case TSlash:
		return "/"
	case TMod:
		return "%"
	case TLt:
		return "<"
	case TLe:
		return "<="
	case TGt:
		return ">"
	case TGe:
		return ">="
	case TEq:
		return "=="
	case TNotEq:
		return "!="
	}
	return ""
}
