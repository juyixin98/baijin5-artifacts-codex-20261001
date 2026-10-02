package frontend

import "fmt"

// Operator precedences (climbing parser).
func prec(op string) int {
	switch op {
	case "||":
		return 1
	case "&&":
		return 2
	case "|":
		return 3
	case "^":
		return 4
	case "&":
		return 5
	case "==", "!=":
		return 6
	case "<", ">", "<=", ">=":
		return 7
	case "+", "-":
		return 8
	case "*", "/", "%":
		return 9
	}
	return 0
}

func (p *parser) parseExpr() (Expr, error) {
	return p.parseBinary(0)
}

func (p *parser) parseUnary() (Expr, error) {
	t := p.peek()
	switch {
	case p.isOp("-"), p.isOp("!"), p.isOp("~"):
		p.next()
		inner, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &UnaryExpr{Op: t.val, Inner: inner, Pos: t.pos}, nil
	case p.isKw("len"):
		p.next()
		if _, err := p.expectOp("("); err != nil {
			return nil, err
		}
		nameT := p.peek()
		if nameT.kind != tIdent {
			return nil, fmt.Errorf("%s: expected name in len(), got %q", nameT.pos, nameT.val)
		}
		p.next()
		if _, err := p.expectOp(")"); err != nil {
			return nil, err
		}
		return &LenExpr{Name: nameT.val, Pos: t.pos}, nil
	case p.isKw("true"):
		p.next()
		return &IntLit{Value: 1, Pos: t.pos}, nil
	case p.isKw("false"):
		p.next()
		return &IntLit{Value: 0, Pos: t.pos}, nil
	case t.kind == tInt:
		p.next()
		var v int64
		for _, r := range t.val {
			v = v*10 + int64(r-'0')
		}
		return &IntLit{Value: v, Pos: t.pos}, nil
	case t.kind == tIdent:
		p.next()
		if p.isOp("[") {
			br := p.next()
			idx, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			if _, err := p.expectOp("]"); err != nil {
				return nil, err
			}
			_ = br
			return &IndexExpr{Name: t.val, Idx: idx, Pos: t.pos}, nil
		}
		return &Ident{Name: t.val, Pos: t.pos}, nil
	case p.isOp("("):
		p.next()
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if _, err := p.expectOp(")"); err != nil {
			return nil, err
		}
		return e, nil
	default:
		return nil, fmt.Errorf("%s: unexpected token %q in expression", t.pos, t.val)
	}
}

func (p *parser) parseBinary(minPrec int) (Expr, error) {
	left, err := p.parseUnary()
	if err != nil {
		return nil, err
	}
	for {
		t := p.peek()
		if t.kind != tOp {
			break
		}
		pr := prec(t.val)
		if pr == 0 || pr < minPrec {
			break
		}
		p.next()
		right, err := p.parseBinary(pr + 1)
		if err != nil {
			return nil, err
		}
		left = &BinaryExpr{Op: t.val, Left: left, Right: right, Pos: t.pos}
	}
	return left, nil
}
