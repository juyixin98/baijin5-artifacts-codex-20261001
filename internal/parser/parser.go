// Package parser turns the token stream into an ast.Program.
package parser

import (
	"genfsm/internal/ast"
	"genfsm/internal/lexer"
	"genfsm/internal/semerr"
)

type Parser struct {
	toks []lexer.Token
	pos  int
}

func Parse(src string) (*ast.Program, error) {
	toks, err := lexer.New(src).Tokenize()
	if err != nil {
		return nil, err
	}
	p := &Parser{toks: toks}
	return p.parseProgram()
}

func (p *Parser) cur() lexer.Token { return p.toks[p.pos] }

func (p *Parser) at(k lexer.Kind) bool { return p.cur().Kind == k }

func (p *Parser) atEnd() bool { return p.cur().Kind == lexer.TEOF }

func (p *Parser) advance() lexer.Token {
	t := p.cur()
	if !p.atEnd() {
		p.pos++
	}
	return t
}

func (p *Parser) expect(k lexer.Kind, what string) (lexer.Token, error) {
	if p.cur().Kind != k {
		return lexer.Token{}, semerr.Input(semerr.CodeParse,
			"expected %s but got %q at %d:%d", what, p.cur().Val, p.cur().Line, p.cur().Col)
	}
	return p.advance(), nil
}

func (p *Parser) tokPos() ast.Pos {
	t := p.cur()
	return ast.Pos{Line: t.Line, Col: t.Col}
}

func (p *Parser) parseProgram() (*ast.Program, error) {
	prog := &ast.Program{P: p.tokPos()}
	for !p.atEnd() {
		fn, err := p.parseFunc()
		if err != nil {
			return nil, err
		}
		prog.Funcs = append(prog.Funcs, fn)
	}
	if err := validate(prog); err != nil {
		return nil, err
	}
	return prog, nil
}

func (p *Parser) parseFunc() (*ast.Func, error) {
	start := p.tokPos()
	isGen := false
	if p.at(lexer.TGen) {
		p.advance()
		isGen = true
		if _, err := p.expect(lexer.TFn, "'fn' after 'gen'"); err != nil {
			return nil, err
		}
	} else {
		if _, err := p.expect(lexer.TFn, "'fn' or 'gen fn'"); err != nil {
			return nil, err
		}
	}
	nameTok, err := p.expect(lexer.TIdent, "function name")
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(lexer.TLParen, "'('"); err != nil {
		return nil, err
	}
	var params []string
	for !p.at(lexer.TRParen) {
		t, err := p.expect(lexer.TIdent, "parameter name")
		if err != nil {
			return nil, err
		}
		params = append(params, t.Val)
		if p.at(lexer.TComma) {
			p.advance()
			continue
		}
		break
	}
	if _, err := p.expect(lexer.TRParen, "')'"); err != nil {
		return nil, err
	}
	body, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	return &ast.Func{Name: nameTok.Val, Params: params, IsGen: isGen, Body: body, P: start}, nil
}

func (p *Parser) parseBlock() (*ast.Block, error) {
	start := p.tokPos()
	if _, err := p.expect(lexer.TLCurly, "'{'"); err != nil {
		return nil, err
	}
	var stmts []ast.Stmt
	for !p.at(lexer.TRCurly) && !p.atEnd() {
		s, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		if s != nil {
			stmts = append(stmts, s)
		}
	}
	if _, err := p.expect(lexer.TRCurly, "'}'"); err != nil {
		return nil, err
	}
	return &ast.Block{Stmts: stmts, P: start}, nil
}

// consumeSemi accepts an explicit ';' or treats a '}'/EOF/line break as one.
func (p *Parser) consumeSemi(prev lexer.Token) error {
	if p.at(lexer.TSemi) {
		p.advance()
		return nil
	}
	if p.at(lexer.TRCurly) || p.atEnd() {
		return nil
	}
	if p.cur().Line > prev.Line {
		return nil
	}
	return semerr.Input(semerr.CodeParse, "missing ';' before %q at %d:%d",
		p.cur().Val, p.cur().Line, p.cur().Col)
}

func (p *Parser) lastTok() lexer.Token {
	if p.pos == 0 {
		return lexer.Token{}
	}
	return p.toks[p.pos-1]
}
