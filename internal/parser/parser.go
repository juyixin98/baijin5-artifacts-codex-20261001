// Package parser builds an ast.Root from tokens and performs frontend
// semantic validation. Stable injection points are assigned here:
//
//	init@N   Nth acquire in source order
//	close@N  the onexit cleanup of acquire N (its optional fail)
//	fail@M   Mth explicit fail statement outside a cleanup block
package parser

import (
	"fmt"
	"strconv"

	"scopelang/internal/ast"
	"scopelang/internal/diag"
	"scopelang/internal/lexer"
)

type Parser struct {
	toks   []lexer.Token
	pos    int
	initNo int
	failNo int
}

func Parse(src string) (*ast.Root, error) {
	toks, err := lexer.Tokenize(src)
	if err != nil {
		return nil, err
	}
	p := &Parser{toks: toks}
	body := &ast.Block{}
	for !p.is(lexer.TEOF) {
		s, perr := p.parseStmt(false)
		if perr != nil {
			return nil, perr
		}
		body.Stmts = append(body.Stmts, s)
	}
	root := &ast.Root{Body: body}
	if err := Validate(root); err != nil {
		return nil, err
	}
	return root, nil
}

func (p *Parser) cur() lexer.Token { return p.toks[p.pos] }

func (p *Parser) is(k lexer.Kind) bool { return p.cur().Kind == k }

func (p *Parser) isWord(w string) bool {
	t := p.cur()
	return t.Kind == lexer.TIdent && t.Val == w
}

func (p *Parser) advance() lexer.Token {
	t := p.cur()
	if p.pos < len(p.toks)-1 {
		p.pos++
	}
	return t
}

func (p *Parser) expectWord(w string) (lexer.Token, error) {
	if !p.isWord(w) {
		t := p.cur()
		return t, p.errAt(t, fmt.Sprintf("expected %q, got %q", w, t.Val))
	}
	return p.advance(), nil
}

func (p *Parser) expect(k lexer.Kind, what string) (lexer.Token, error) {
	if !p.is(k) {
		t := p.cur()
		return t, p.errAt(t, fmt.Sprintf("expected %s, got %q", what, t.Val))
	}
	return p.advance(), nil
}

func (p *Parser) errAt(t lexer.Token, msg string) *diag.Error {
	return diag.New(diag.Input, "PARSE", fmt.Sprintf("%d:%d: %s", t.Line, t.Col, msg)).
		With(diag.PhaseFrontend, "", 0)
}

func (p *Parser) skipSemicolons() {
	for p.is(lexer.TSemicolon) {
		p.advance()
	}
}

func toPos(t lexer.Token) ast.Pos {
	return ast.Pos{Line: t.Line, Column: t.Col, Off: t.Off}
}

// parseStmt parses one statement. inCleanup restricts the grammar to
// emit/fail and forbids control transfer.
func (p *Parser) parseStmt(inCleanup bool) (ast.Stmt, error) {
	p.skipSemicolons()
	t := p.cur()
	switch {
	case p.isWord("let"):
		if inCleanup {
			return nil, p.errAt(t, "acquire is not allowed inside onexit cleanup")
		}
		return p.parseAcquire()
	case p.isWord("scope"):
		if inCleanup {
			return nil, p.errAt(t, "scope is not allowed inside onexit cleanup")
		}
		p.advance()
		blk, err := p.parseBraceBlock(true)
		if err != nil {
			return nil, err
		}
		return blk, nil
	case p.isWord("repeat"):
		if inCleanup {
			return nil, p.errAt(t, "repeat is not allowed inside onexit cleanup")
		}
		return p.parseRepeat()
	case p.isWord("emit"):
		return p.parseEmit()
	case p.isWord("fail"):
		return p.parseFail()
	case p.isWord("break"):
		if inCleanup {
			return nil, p.errAt(t, "break is not allowed inside onexit cleanup")
		}
		p.advance()
		return &ast.Break{Line: t.Line, Pos: toPos(t)}, nil
	case p.isWord("return"):
		if inCleanup {
			return nil, p.errAt(t, "return is not allowed inside onexit cleanup")
		}
		return p.parseReturn()
	default:
		return nil, p.errAt(t, fmt.Sprintf("unexpected token %q", t.Val))
	}
}

func (p *Parser) parseAcquire() (ast.Stmt, error) {
	let := p.advance()
	nameTok, err := p.expect(lexer.TIdent, "resource name")
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TEq, "\"=\""); err != nil {
		return nil, err
	}
	if _, err := p.expectWord("acquire"); err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TLParen, "\"(\""); err != nil {
		return nil, err
	}
	exprTok, err := p.expect(lexer.TString, "string resource expression")
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TRParen, "\")\""); err != nil {
		return nil, err
	}
	if _, err := p.expectWord("onexit"); err != nil {
		return nil, err
	}
	p.initNo++
	n := p.initNo
	a := &ast.Acquire{
		Name:    nameTok.Val,
		ResExpr: exprTok.Val,
		Point:   fmt.Sprintf("init@%d", n),
		Line:    let.Line,
		Start:   toPos(let),
	}
	cleanup, err := p.parseCleanup(n)
	if err != nil {
		return nil, err
	}
	a.Cleanup = cleanup
	return a, nil
}

func (p *Parser) parseCleanup(initOrdinal int) (*ast.Block, error) {
	lb, err := p.expect(lexer.TLBrace, "\"{\"")
	if err != nil {
		return nil, err
	}
	blk := &ast.Block{LBrace: toPos(lb), RBrace: toPos(lb), Named: false}
	sawFail := false
	for {
		p.skipSemicolons()
		if p.is(lexer.TRBrace) {
			rb := p.advance()
			blk.RBrace = toPos(rb)
			return blk, nil
		}
		if p.is(lexer.TEOF) {
			return nil, p.errAt(p.cur(), "unterminated onexit block, expected \"}\"")
		}
		s, err := p.parseStmt(true)
		if err != nil {
			return nil, err
		}
		if f, ok := s.(*ast.Fail); ok {
			if sawFail {
				return nil, p.errAt(p.cur(), "onexit block may contain at most one fail")
			}
			sawFail = true
			f.Point = fmt.Sprintf("close@%d", initOrdinal)
		}
		blk.Stmts = append(blk.Stmts, s)
	}
}

func (p *Parser) parseBraceBlock(named bool) (*ast.Block, error) {
	lb, err := p.expect(lexer.TLBrace, "\"{\"")
	if err != nil {
		return nil, err
	}
	blk := &ast.Block{LBrace: toPos(lb), Named: named}
	for {
		p.skipSemicolons()
		if p.is(lexer.TRBrace) {
			rb := p.advance()
			blk.RBrace = toPos(rb)
			return blk, nil
		}
		if p.is(lexer.TEOF) {
			return nil, p.errAt(p.cur(), "unterminated block, expected \"}\"")
		}
		s, err := p.parseStmt(false)
		if err != nil {
			return nil, err
		}
		blk.Stmts = append(blk.Stmts, s)
	}
}

func (p *Parser) parseRepeat() (ast.Stmt, error) {
	rep := p.advance()
	countTok, err := p.expect(lexer.TInt, "non-negative iteration count")
	if err != nil {
		return nil, err
	}
	if _, err := p.expectWord("times"); err != nil {
		return nil, err
	}
	count, convErr := strconv.Atoi(countTok.Val)
	if convErr != nil {
		return nil, p.errAt(countTok, "iteration count is not a valid integer")
	}
	body, err := p.parseBraceBlock(false)
	if err != nil {
		return nil, err
	}
	return &ast.Repeat{Count: count, Body: body, Line: rep.Line, Pos: toPos(rep)}, nil
}

func (p *Parser) parseEmit() (ast.Stmt, error) {
	em := p.advance()
	textTok, err := p.expect(lexer.TString, "string after emit")
	if err != nil {
		return nil, err
	}
	return &ast.Emit{Text: textTok.Val, Line: em.Line, Pos: toPos(em)}, nil
}

func (p *Parser) parseFail() (ast.Stmt, error) {
	f := p.advance()
	p.failNo++
	return &ast.Fail{Point: fmt.Sprintf("fail@%d", p.failNo), Line: f.Line, Pos: toPos(f)}, nil
}

func (p *Parser) parseReturn() (ast.Stmt, error) {
	r := p.advance()
	stmt := &ast.Return{Line: r.Line, Pos: toPos(r)}
	p.skipSemicolons()
	if p.is(lexer.TString) {
		stmt.Value = p.advance().Val
	}
	return stmt, nil
}
