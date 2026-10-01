package frontend

import (
	"genstatemachine/internal/gerr"
)

// Parser turns tokens into an AST Program.
type Parser struct {
	toks []Token
	pos  int
}

// Parse tokenizes and parses src into a Program.
func Parse(src string) (*Program, error) {
	toks, err := Lex(src)
	if err != nil {
		return nil, err
	}
	p := &Parser{toks: toks}
	return p.program()
}

func (p *Parser) cur() Token          { return p.toks[p.pos] }
func (p *Parser) at(k TokenKind) bool { return p.cur().Kind == k }

func (p *Parser) advanceTok() Token {
	t := p.cur()
	if p.pos < len(p.toks)-1 {
		p.pos++
	}
	return t
}

func (p *Parser) expect(k TokenKind, what string) (Token, error) {
	if p.cur().Kind != k {
		return Token{}, gerr.New(gerr.EParse, "expected %s but got %q", what, p.cur().Val).
			AtPos(p.cur().Line, p.cur().Col)
	}
	return p.advanceTok(), nil
}

// eatStmtSep consumes one optional semicolon; ASI is explicit in our grammar
// because braces/newlines are otherwise ignored by the lexer, so statements
// must simply end with ';' except blocks.
func (p *Parser) eatSemi() error {
	if p.at(TSemicol) {
		p.advanceTok()
		return nil
	}
	return gerr.New(gerr.EParse, "expected ';' after statement but got %q", p.cur().Val).
		AtPos(p.cur().Line, p.cur().Col)
}

func (p *Parser) program() (*Program, error) {
	prog := &Program{}
	seen := map[string]bool{}
	for !p.at(TEOF) {
		if !p.at(KGen) {
			return nil, gerr.New(gerr.EParse, "expected top-level 'gen' declaration but got %q", p.cur().Val).
				AtPos(p.cur().Line, p.cur().Col)
		}
		g, err := p.genDecl()
		if err != nil {
			return nil, err
		}
		if seen[g.Name] {
			return nil, gerr.New(gerr.EDupGen, "duplicate generator %q", g.Name).AtPos(g.Line, 0)
		}
		seen[g.Name] = true
		prog.Gens = append(prog.Gens, g)
	}
	if len(prog.Gens) == 0 {
		return nil, gerr.New(gerr.EValidate, "program contains no generator declarations")
	}
	return prog, nil
}

func (p *Parser) genDecl() (*GenDecl, error) {
	kw := p.advanceTok() // gen
	name, err := p.expect(TIdent, "generator name")
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(TLBrace, "'{'"); err != nil {
		return nil, err
	}
	var body []Stmt
	for !p.at(TRBrace) && !p.at(TEOF) {
		s, err := p.stmt()
		if err != nil {
			return nil, err
		}
		body = append(body, s)
	}
	if _, err := p.expect(TRBrace, "'}'"); err != nil {
		return nil, err
	}
	return &GenDecl{Name: name.Val, Line: kw.Line, Body: body}, nil
}

// blockBody parses statements until the closing brace.
func (p *Parser) blockBody(closeTok TokenKind) ([]Stmt, error) {
	var list []Stmt
	for !p.at(closeTok) && !p.at(TEOF) {
		s, err := p.stmt()
		if err != nil {
			return nil, err
		}
		list = append(list, s)
	}
	return list, nil
}

func (p *Parser) bracedBlock() ([]Stmt, Token, error) {
	brace, err := p.expect(TLBrace, "'{'")
	if err != nil {
		return nil, Token{}, err
	}
	list, err := p.blockBody(TRBrace)
	if err != nil {
		return nil, Token{}, err
	}
	closeT, err := p.expect(TRBrace, "'}'")
	if err != nil {
		return nil, Token{}, err
	}
	_ = brace
	return list, closeT, nil
}

func (p *Parser) stmt() (Stmt, error) {
	t := p.cur()
	switch t.Kind {
	case TLBrace:
		p.advanceTok()
		list, err := p.blockBody(TRBrace)
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(TRBrace, "'}'"); err != nil {
			return nil, err
		}
		return &BlockStmt{Pos: Pos{t.Line}, List: list}, nil
	case KVar:
		return p.varStmt()
	case KYield:
		return p.yieldStmt()
	case KReturn:
		return p.returnStmt()
	case KThrow:
		return p.throwStmt()
	case KIf:
		return p.ifStmt()
	case KWhile:
		return p.whileStmt()
	case KTry:
		return p.tryStmt()
	default:
		return p.exprOrAssignStmt()
	}
}

func (p *Parser) varStmt() (Stmt, error) {
	t := p.advanceTok() // var
	name, err := p.expect(TIdent, "variable name")
	if err != nil {
		return nil, err
	}
	s := &VarStmt{Pos: Pos{t.Line}, Name: name.Val}
	if p.at(TAssign) {
		p.advanceTok()
		init, err := p.expression()
		if err != nil {
			return nil, err
		}
		s.Init = init
	}
	if err := p.eatSemi(); err != nil {
		return nil, err
	}
	return s, nil
}

func (p *Parser) yieldStmt() (Stmt, error) {
	t := p.advanceTok() // yield
	s := &YieldStmt{Pos: Pos{t.Line}}
	if !p.at(TSemicol) {
		e, err := p.expression()
		if err != nil {
			return nil, err
		}
		s.Expr = e
	}
	if err := p.eatSemi(); err != nil {
		return nil, err
	}
	return s, nil
}

func (p *Parser) returnStmt() (Stmt, error) {
	t := p.advanceTok() // return
	s := &ReturnStmt{Pos: Pos{t.Line}}
	if !p.at(TSemicol) {
		e, err := p.expression()
		if err != nil {
			return nil, err
		}
		s.Expr = e
	}
	if err := p.eatSemi(); err != nil {
		return nil, err
	}
	return s, nil
}

func (p *Parser) throwStmt() (Stmt, error) {
	t := p.advanceTok() // throw
	e, err := p.expression()
	if err != nil {
		return nil, err
	}
	if err := p.eatSemi(); err != nil {
		return nil, err
	}
	return &ThrowStmt{Pos: Pos{t.Line}, Expr: e}, nil
}

func (p *Parser) ifStmt() (Stmt, error) {
	t := p.advanceTok() // if
	if _, err := p.expect(TLParen, "'('"); err != nil {
		return nil, err
	}
	cond, err := p.expression()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	thenB, _, err := p.bracedBlock()
	if err != nil {
		return nil, err
	}
	s := &IfStmt{Pos: Pos{t.Line}, Cond: cond, Then: thenB}
	if p.at(KElse) {
		p.advanceTok()
		if p.at(KIf) {
			inner, err := p.stmt()
			if err != nil {
				return nil, err
			}
			s.Else = []Stmt{inner}
		} else {
			elseB, _, err := p.bracedBlock()
			if err != nil {
				return nil, err
			}
			s.Else = elseB
		}
	}
	return s, nil
}

func (p *Parser) whileStmt() (Stmt, error) {
	t := p.advanceTok() // while
	if _, err := p.expect(TLParen, "'('"); err != nil {
		return nil, err
	}
	cond, err := p.expression()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	body, _, err := p.bracedBlock()
	if err != nil {
		return nil, err
	}
	return &WhileStmt{Pos: Pos{t.Line}, Cond: cond, Body: body}, nil
}

func (p *Parser) tryStmt() (Stmt, error) {
	t := p.advanceTok() // try
	body, _, err := p.bracedBlock()
	if err != nil {
		return nil, err
	}
	s := &TryStmt{Pos: Pos{t.Line}, Body: body}
	for p.at(KCatch) {
		c, err := p.catchClause()
		if err != nil {
			return nil, err
		}
		s.Catches = append(s.Catches, c)
	}
	if p.at(KFinally) {
		p.advanceTok()
		fin, _, err := p.bracedBlock()
		if err != nil {
			return nil, err
		}
		s.Finally = fin
	}
	if len(s.Catches) == 0 && len(s.Finally) == 0 {
		return nil, gerr.New(gerr.EParse, "try requires at least one catch or finally clause").
			AtPos(t.Line, 0)
	}
	return s, nil
}

func (p *Parser) catchClause() (*CatchClause, error) {
	t := p.advanceTok() // catch
	if _, err := p.expect(TLParen, "'('"); err != nil {
		return nil, err
	}
	name, err := p.expect(TIdent, "catch binding name")
	if err != nil {
		return nil, err
	}
	c := &CatchClause{Pos: Pos{t.Line}, Name: name.Val}
	if p.at(TAssign) {
		p.advanceTok()
		test, err := p.expression()
		if err != nil {
			return nil, err
		}
		c.Test = test
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	body, _, err := p.bracedBlock()
	if err != nil {
		return nil, err
	}
	c.Body = body
	return c, nil
}

func (p *Parser) exprOrAssignStmt() (Stmt, error) {
	// IDENT '=' expr ';'  => assignment; otherwise expression statement.
	t := p.cur()
	if t.Kind == TIdent && p.toks[p.pos+1].Kind == TAssign {
		name := p.advanceTok()
		p.advanceTok() // =
		e, err := p.expression()
		if err != nil {
			return nil, err
		}
		if err := p.eatSemi(); err != nil {
			return nil, err
		}
		return &AssignStmt{Pos: Pos{name.Line}, Name: name.Val, Expr: e}, nil
	}
	e, err := p.expression()
	if err != nil {
		return nil, err
	}
	if err := p.eatSemi(); err != nil {
		return nil, err
	}
	return &ExprStmt{Pos: Pos{t.Line}, Expr: e}, nil
}
