package frontend

import (
	"strconv"

	"genstatemachine/internal/gerr"
)

// expression entry: lowest precedence is logical-or.
func (p *Parser) expression() (Expr, error) { return p.parseOr() }

func (p *Parser) parseOr() (Expr, error) {
	lhs, err := p.parseAnd()
	if err != nil {
		return nil, err
	}
	for p.at(TOr) {
		t := p.advanceTok()
		rhs, err := p.parseAnd()
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Pos: Pos{t.Line}, Op: "||", LHS: lhs, RHS: rhs}
	}
	return lhs, nil
}

func (p *Parser) parseAnd() (Expr, error) {
	lhs, err := p.parseEquality()
	if err != nil {
		return nil, err
	}
	for p.at(TAnd) {
		t := p.advanceTok()
		rhs, err := p.parseEquality()
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Pos: Pos{t.Line}, Op: "&&", LHS: lhs, RHS: rhs}
	}
	return lhs, nil
}

func (p *Parser) parseEquality() (Expr, error) {
	lhs, err := p.parseRelational()
	if err != nil {
		return nil, err
	}
	for p.at(TEq) || p.at(TNe) {
		t := p.advanceTok()
		op := "=="
		if t.Kind == TNe {
			op = "!="
		}
		rhs, err := p.parseRelational()
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Pos: Pos{t.Line}, Op: op, LHS: lhs, RHS: rhs}
	}
	return lhs, nil
}

func (p *Parser) parseRelational() (Expr, error) {
	lhs, err := p.parseAdditive()
	if err != nil {
		return nil, err
	}
	for p.at(TLt) || p.at(TLe) || p.at(TGt) || p.at(TGe) {
		t := p.advanceTok()
		var op string
		switch t.Kind {
		case TLt:
			op = "<"
		case TLe:
			op = "<="
		case TGt:
			op = ">"
		case TGe:
			op = ">="
		}
		rhs, err := p.parseAdditive()
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Pos: Pos{t.Line}, Op: op, LHS: lhs, RHS: rhs}
	}
	return lhs, nil
}

func (p *Parser) parseAdditive() (Expr, error) {
	lhs, err := p.parseMul()
	if err != nil {
		return nil, err
	}
	for p.at(TPlus) || p.at(TMINUS) {
		t := p.advanceTok()
		op := "+"
		if t.Kind == TMINUS {
			op = "-"
		}
		rhs, err := p.parseMul()
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Pos: Pos{t.Line}, Op: op, LHS: lhs, RHS: rhs}
	}
	return lhs, nil
}

func (p *Parser) parseMul() (Expr, error) {
	lhs, err := p.parseUnary()
	if err != nil {
		return nil, err
	}
	for p.at(TStar) || p.at(TSlash) || p.at(TPct) {
		t := p.advanceTok()
		var op string
		switch t.Kind {
		case TStar:
			op = "*"
		case TSlash:
			op = "/"
		case TPct:
			op = "%"
		}
		rhs, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		lhs = &BinaryExpr{Pos: Pos{t.Line}, Op: op, LHS: lhs, RHS: rhs}
	}
	return lhs, nil
}

func (p *Parser) parseUnary() (Expr, error) {
	if p.at(TBang) || p.at(TMINUS) {
		t := p.advanceTok()
		op := "!"
		if t.Kind == TMINUS {
			op = "-"
		}
		e, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &UnaryExpr{Pos: Pos{t.Line}, Op: op, Expr: e}, nil
	}
	return p.primary()
}

func (p *Parser) primary() (Expr, error) {
	t := p.cur()
	switch t.Kind {
	case TInt:
		p.advanceTok()
		n, err := strconv.ParseInt(t.Val, 10, 64)
		if err != nil {
			return nil, gerr.New(gerr.EParse, "invalid integer %q", t.Val).AtPos(t.Line, t.Col)
		}
		return &IntLit{Pos: Pos{t.Line}, Value: n}, nil
	case TStr:
		p.advanceTok()
		return &StrLit{Pos: Pos{t.Line}, Value: t.Val}, nil
	case KTrue:
		p.advanceTok()
		return &BoolLit{Pos: Pos{t.Line}, Value: true}, nil
	case KFalse:
		p.advanceTok()
		return &BoolLit{Pos: Pos{t.Line}, Value: false}, nil
	case KNil:
		p.advanceTok()
		return &NilLit{Pos: Pos{t.Line}}, nil
	case TIdent:
		p.advanceTok()
		if p.at(TLParen) {
			return p.call(t)
		}
		return &Ident{Pos: Pos{t.Line}, Name: t.Val}, nil
	case TLParen:
		p.advanceTok()
		e, err := p.expression()
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(TRParen, "')'"); err != nil {
			return nil, err
		}
		return e, nil
	default:
		return nil, gerr.New(gerr.EParse, "unexpected token %q in expression", t.Val).
			AtPos(t.Line, t.Col)
	}
}

func (p *Parser) call(nameTok Token) (Expr, error) {
	if _, err := p.expect(TLParen, "'('"); err != nil {
		return nil, err
	}
	c := &CallExpr{Pos: Pos{nameTok.Line}, Name: nameTok.Val}
	if !p.at(TRParen) {
		for {
			a, err := p.expression()
			if err != nil {
				return nil, err
			}
			c.Args = append(c.Args, a)
			if !p.at(TComma) {
				break
			}
			p.advanceTok()
		}
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	return c, nil
}
