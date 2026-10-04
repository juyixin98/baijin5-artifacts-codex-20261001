package parser

import (
	"genfsm/internal/ast"
	"genfsm/internal/lexer"
	"genfsm/internal/semerr"
)

func (p *Parser) parseStmt() (ast.Stmt, error) {
	switch p.cur().Kind {
	case lexer.TLet:
		return p.parseLet()
	case lexer.TIf:
		return p.parseIf()
	case lexer.TWhile:
		return p.parseWhile()
	case lexer.TFor:
		return p.parseFor()
	case lexer.TReturn:
		return p.parseReturn()
	case lexer.TThrow:
		return p.parseThrow()
	case lexer.TTry:
		return p.parseTry()
	case lexer.TRCurly:
		return nil, semerr.Input(semerr.CodeParse, "unexpected '}' at %d:%d", p.cur().Line, p.cur().Col)
	default:
		return p.parseExprOrAssignStmt()
	}
}

func (p *Parser) parseLet() (*ast.LetStmt, error) {
	start := p.tokPos()
	p.advance() // let
	nameTok, err := p.expect(lexer.TIdent, "variable name after 'let'")
	if err != nil {
		return nil, err
	}
	var init ast.Expr
	if p.at(lexer.TAssign) {
		p.advance()
		init, err = p.parseExpr()
		if err != nil {
			return nil, err
		}
	}
	if err := p.consumeSemi(p.lastTok()); err != nil {
		return nil, err
	}
	return &ast.LetStmt{Name: nameTok.Val, Init: init, P: start}, nil
}

func (p *Parser) parseExprOrAssignStmt() (ast.Stmt, error) {
	start := p.tokPos()
	first, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if p.at(lexer.TAssign) {
		p.advance()
		rhs, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		name, ok := first.(*ast.NameExpr)
		if !ok {
			return nil, semerr.Input(semerr.CodeParse, "assignment target must be a name at %d:%d", start.Line, start.Col)
		}
		if err := p.consumeSemi(p.lastTok()); err != nil {
			return nil, err
		}
		return &ast.AssignStmt{Target: name, Value: rhs, P: start}, nil
	}
	if err := p.consumeSemi(p.lastTok()); err != nil {
		return nil, err
	}
	return &ast.ExprStmt{X: first, P: start}, nil
}

func (p *Parser) parseIf() (*ast.IfStmt, error) {
	start := p.tokPos()
	p.advance() // if
	if _, err := p.expect(lexer.TLParen, "'(' after 'if'"); err != nil {
		return nil, err
	}
	cond, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TRParen, "')'"); err != nil {
		return nil, err
	}
	then, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	var elseBranch ast.Stmt
	if p.at(lexer.TElse) {
		p.advance()
		if p.at(lexer.TIf) {
			elseBranch, err = p.parseIf()
			if err != nil {
				return nil, err
			}
		} else {
			b, err := p.parseBlock()
			if err != nil {
				return nil, err
			}
			elseBranch = b
		}
	}
	return &ast.IfStmt{Cond: cond, Then: then, Else: elseBranch, P: start}, nil
}

func (p *Parser) parseWhile() (*ast.WhileStmt, error) {
	start := p.tokPos()
	p.advance()
	if _, err := p.expect(lexer.TLParen, "'(' after 'while'"); err != nil {
		return nil, err
	}
	cond, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TRParen, "')'"); err != nil {
		return nil, err
	}
	body, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	return &ast.WhileStmt{Cond: cond, Body: body, P: start}, nil
}

func (p *Parser) parseFor() (*ast.ForStmt, error) {
	start := p.tokPos()
	p.advance()
	varTok, err := p.expect(lexer.TIdent, "loop variable after 'for'")
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TIn, "'in'"); err != nil {
		return nil, err
	}
	iter, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	body, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	return &ast.ForStmt{Var: varTok.Val, Iter: iter, Body: body, P: start}, nil
}

func (p *Parser) parseReturn() (*ast.ReturnStmt, error) {
	start := p.tokPos()
	p.advance()
	if p.at(lexer.TSemi) {
		p.advance()
		return &ast.ReturnStmt{Value: nil, P: start}, nil
	}
	if p.at(lexer.TRCurly) || p.atEnd() || p.cur().Line > start.Line {
		return &ast.ReturnStmt{Value: nil, P: start}, nil
	}
	v, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if err := p.consumeSemi(p.lastTok()); err != nil {
		return nil, err
	}
	return &ast.ReturnStmt{Value: v, P: start}, nil
}

func (p *Parser) parseThrow() (*ast.ThrowStmt, error) {
	start := p.tokPos()
	p.advance()
	v, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if err := p.consumeSemi(p.lastTok()); err != nil {
		return nil, err
	}
	return &ast.ThrowStmt{Value: v, P: start}, nil
}

func (p *Parser) parseTry() (*ast.TryStmt, error) {
	start := p.tokPos()
	p.advance()
	body, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	var catch *ast.CatchClause
	var fin *ast.Block
	if p.at(lexer.TCatch) {
		cStart := p.tokPos()
		p.advance()
		if _, err := p.expect(lexer.TLParen, "'(' after 'catch'"); err != nil {
			return nil, err
		}
		param, err := p.expect(lexer.TIdent, "catch parameter")
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(lexer.TRParen, "')'"); err != nil {
			return nil, err
		}
		cb, err := p.parseBlock()
		if err != nil {
			return nil, err
		}
		catch = &ast.CatchClause{Param: param.Val, Body: cb, P: cStart}
	}
	if p.at(lexer.TFinally) {
		p.advance()
		fin, err = p.parseBlock()
		if err != nil {
			return nil, err
		}
	}
	if catch == nil && fin == nil {
		return nil, semerr.Input(semerr.CodeParse, "'try' requires a catch or finally clause at %d:%d", start.Line, start.Col)
	}
	return &ast.TryStmt{Body: body, Catch: catch, Finally: fin, P: start}, nil
}
