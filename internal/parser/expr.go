package parser

import (
	"strconv"

	"genfsm/internal/ast"
	"genfsm/internal/lexer"
	"genfsm/internal/semerr"
)

// parseExpr parses a full expression with precedence climbing.
func (p *Parser) parseExpr() (ast.Expr, error) {
	return p.parseBinary(0)
}

func binaryPrec(k lexer.Kind) int {
	switch k {
	case lexer.TOr:
		return 1
	case lexer.TAnd:
		return 2
	case lexer.TEq, lexer.TNeq:
		return 3
	case lexer.TLt, lexer.TLe, lexer.TGt, lexer.TGe:
		return 4
	case lexer.TPlus, lexer.TMinus:
		return 5
	case lexer.TStar, lexer.TSlash, lexer.TPercent:
		return 6
	}
	return -1
}

func (p *Parser) parseBinary(minPrec int) (ast.Expr, error) {
	left, err := p.parseUnary()
	if err != nil {
		return nil, err
	}
	for {
		prec := binaryPrec(p.cur().Kind)
		if prec < minPrec {
			return left, nil
		}
		opTok := p.advance()
		right, err := p.parseBinary(prec + 1)
		if err != nil {
			return nil, err
		}
		op := opTok.Val
		short := opTok.Kind == lexer.TAnd || opTok.Kind == lexer.TOr
		left = &ast.BinaryExpr{Op: op, X: left, Y: right, P: ast.Pos{Line: opTok.Line, Col: opTok.Col}, ShortCircuit: short}
	}
}

func (p *Parser) parseUnary() (ast.Expr, error) {
	if p.at(lexer.TYield) {
		t := p.advance()
		var inner ast.Expr
		// Yield binds like a prefix operator: yield EXPR where EXPR is a
		// unary-level expression, so "yield i" stops before a following
		// statement but "yield i + 1" yields the whole sum.
		if !p.at(lexer.TSemi) && !p.at(lexer.TRCurly) && !p.atEnd() &&
			p.cur().Line == t.Line {
			var err error
			inner, err = p.parseBinary(7)
			if err != nil {
				return nil, err
			}
		}
		return &ast.YieldExpr{Init: inner, P: ast.Pos{Line: t.Line, Col: t.Col}}, nil
	}
	if p.at(lexer.TMinus) || p.at(lexer.TBang) || p.at(lexer.TPlus) {
		t := p.advance()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &ast.UnaryExpr{Op: t.Val, X: x, P: ast.Pos{Line: t.Line, Col: t.Col}}, nil
	}
	return p.parsePrimary()
}

func (p *Parser) parsePrimary() (ast.Expr, error) {
	t := p.cur()
	pos := ast.Pos{Line: t.Line, Col: t.Col}
	switch t.Kind {
	case lexer.TInt:
		p.advance()
		n, err := strconv.ParseInt(t.Val, 10, 64)
		if err != nil {
			return nil, semerr.Input(semerr.CodeParse, "invalid integer %q at %d:%d", t.Val, t.Line, t.Col)
		}
		return &ast.IntLit{Value: n, P: pos}, nil
	case lexer.TStr:
		p.advance()
		return &ast.StrLit{Value: t.Val, P: pos}, nil
	case lexer.TNull:
		p.advance()
		return &ast.NullLit{P: pos}, nil
	case lexer.TTrue:
		p.advance()
		return &ast.BoolLit{Value: true, P: pos}, nil
	case lexer.TFalse:
		p.advance()
		return &ast.BoolLit{Value: false, P: pos}, nil
	case lexer.TIdent:
		p.advance()
		if p.at(lexer.TLParen) {
			return p.parseCall(t.Val, pos)
		}
		return &ast.NameExpr{Name: t.Val, P: pos}, nil
	case lexer.TLParen:
		p.advance()
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(lexer.TRParen, "')'"); err != nil {
			return nil, err
		}
		return e, nil
	default:
		return nil, semerr.Input(semerr.CodeParse, "unexpected token %q at %d:%d", t.Val, t.Line, t.Col)
	}
}

func (p *Parser) parseCall(name string, pos ast.Pos) (ast.Expr, error) {
	p.advance() // (
	var args []ast.Expr
	for !p.at(lexer.TRParen) {
		a, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		args = append(args, a)
		if p.at(lexer.TComma) {
			p.advance()
			continue
		}
		break
	}
	if _, err := p.expect(lexer.TRParen, "')' after arguments"); err != nil {
		return nil, err
	}
	return &ast.CallExpr{Callee: name, Args: args, P: pos}, nil
}
