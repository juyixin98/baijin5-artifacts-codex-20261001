package syntax

import (
	"strconv"

	"funcspec/internal/errcat"
)

type parser struct {
	tokens []Token
	pos    int
}

// Parse parses a whole source program.
func Parse(src string) (*Program, error) {
	toks, err := Lex(src)
	if err != nil {
		return nil, err
	}
	p := &parser{tokens: toks}
	prog := &Program{}
	for !p.at(TEOF) {
		fn, err := p.parseFunc()
		if err != nil {
			return nil, err
		}
		prog.Funcs = append(prog.Funcs, fn)
	}
	if len(prog.Funcs) == 0 {
		return nil, errcat.New(errcat.Syntax, "program must contain at least one function at %s", p.cur().Pos)
	}
	return prog, nil
}

func (p *parser) cur() Token  { return p.tokens[p.pos] }
func (p *parser) peek() Token { return p.tokens[p.pos+1] }

func (p *parser) at(k TokenKind) bool { return p.cur().Kind == k }

func (p *parser) advanceTok() Token {
	t := p.cur()
	p.pos++
	return t
}

func (p *parser) expect(k TokenKind, what string) (Token, error) {
	if p.cur().Kind != k {
		return Token{}, errcat.New(errcat.Syntax, "expected %s but got %q at %s", what, p.cur().Lit, p.cur().Pos)
	}
	return p.advanceTok(), nil
}

// acceptSemicolon consumes an explicit or automatic semicolon.
func (p *parser) acceptSemicolon() {
	if p.at(TSemicolon) {
		p.advanceTok()
	}
}

// expectTerminator requires a semicolon (explicit or auto-inserted) unless the
// next token is "}" or EOF.
func (p *parser) expectTerminator() error {
	if p.at(TSemicolon) {
		p.advanceTok()
		return nil
	}
	if p.at(TRBrace) || p.at(TEOF) {
		return nil
	}
	return errcat.New(errcat.Syntax, "missing semicolon or newline before %q at %s", p.cur().Lit, p.cur().Pos)
}

func isKeyword(lit string) bool {
	switch lit {
	case "fn", "pure", "if", "else", "return", "let", "true", "false":
		return true
	}
	return false
}

func (p *parser) parseFunc() (*FuncDecl, error) {
	start := p.cur().Pos
	pure := false
	if p.cur().Kind == TIdent && p.cur().Lit == "pure" {
		pure = true
		p.advanceTok()
	}
	if _, err := p.expectKeyword("fn"); err != nil {
		return nil, err
	}
	nameTok, err := p.expect(TIdent, "function name")
	if err != nil {
		return nil, err
	}
	if isKeyword(nameTok.Lit) {
		return nil, errcat.New(errcat.Syntax, "function name %q is a keyword at %s", nameTok.Lit, nameTok.Pos)
	}
	if _, err := p.expect(TLParen, "'('"); err != nil {
		return nil, err
	}
	var params []string
	if !p.at(TRParen) {
		for {
			tok, err := p.expect(TIdent, "parameter name")
			if err != nil {
				return nil, err
			}
			if isKeyword(tok.Lit) {
				return nil, errcat.New(errcat.Syntax, "parameter name %q is a keyword at %s", tok.Lit, tok.Pos)
			}
			params = append(params, tok.Lit)
			if p.at(TComma) {
				p.advanceTok()
				continue
			}
			break
		}
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	body, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	return &FuncDecl{Name: nameTok.Lit, Params: params, Pure: pure, Body: body, Pos: start}, nil
}

func (p *parser) expectKeyword(kw string) (Token, error) {
	if p.cur().Kind != TIdent || p.cur().Lit != kw {
		return Token{}, errcat.New(errcat.Syntax, "expected %q but got %q at %s", kw, p.cur().Lit, p.cur().Pos)
	}
	return p.advanceTok(), nil
}

func (p *parser) parseBlock() ([]Stmt, error) {
	if _, err := p.expect(TLBrace, "'{'"); err != nil {
		return nil, err
	}
	p.acceptSemicolon()
	var stmts []Stmt
	for !p.at(TRBrace) && !p.at(TEOF) {
		s, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		stmts = append(stmts, s)
	}
	if _, err := p.expect(TRBrace, "'}'"); err != nil {
		return nil, err
	}
	return stmts, nil
}

func (p *parser) parseStmt() (Stmt, error) {
	start := p.cur().Pos
	switch {
	case p.cur().Kind == TIdent && p.cur().Lit == "let":
		p.advanceTok()
		name, err := p.expect(TIdent, "binding name")
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(TEq, "'='"); err != nil {
			return nil, err
		}
		init, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if err := p.expectTerminator(); err != nil {
			return nil, err
		}
		return &LetStmt{Name: name.Lit, Init: init, Pos: start}, nil
	case p.cur().Kind == TIdent && p.cur().Lit == "return":
		p.advanceTok()
		if p.stmtBoundary() {
			return &ReturnStmt{Value: nil, Pos: start}, p.terminate()
		}
		v, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if err := p.expectTerminator(); err != nil {
			return nil, err
		}
		return &ReturnStmt{Value: v, Pos: start}, nil
	case p.cur().Kind == TIdent && p.cur().Lit == "if":
		return p.parseIf(start)
	default:
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if err := p.expectTerminator(); err != nil {
			return nil, err
		}
		return &ExprStmt{X: e, Pos: start}, nil
	}
}

func (p *parser) stmtBoundary() bool {
	return p.at(TSemicolon) || p.at(TRBrace) || p.at(TEOF)
}

func (p *parser) terminate() error {
	if p.at(TSemicolon) {
		p.advanceTok()
	}
	return nil
}

func (p *parser) parseIf(start Pos) (Stmt, error) {
	p.advanceTok() // if
	if _, err := p.expect(TLParen, "'(' after if"); err != nil {
		return nil, err
	}
	cond, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	thenB, err := p.parseBlock()
	if err != nil {
		return nil, err
	}
	var elseB []Stmt
	// The lexer inserts a virtual semicolon on a newline before `else`;
	// else is only recognized when attached without a separating newline.
	if p.cur().Kind == TIdent && p.cur().Lit == "else" {
		p.advanceTok()
		if p.cur().Kind == TIdent && p.cur().Lit == "if" {
			s, err := p.parseIf(p.cur().Pos)
			if err != nil {
				return nil, err
			}
			elseB = []Stmt{s}
		} else {
			elseB, err = p.parseBlock()
			if err != nil {
				return nil, err
			}
		}
	}
	return &IfStmt{Cond: cond, Then: thenB, Else: elseB, Pos: start}, nil
}

// Expression grammar (precedence low -> high):
//
//	or  := and ('||' and)*
//	and := eq ('&&' eq)*
//	eq  := rel (('==' | '!=') rel)*
//	rel := add (('<' | '>' | '<=' | '>=') add)*
//	add := mul (('+' | '-') mul)*
//	mul := unary (('*' | '/' | '%') unary)*
//	unary := ('-' | '!') unary | primary
func (p *parser) parseExpr() (Expr, error) { return p.parseOr() }

func (p *parser) binaryLoop(sub func() (Expr, error), ops map[string]bool) (Expr, error) {
	left, err := sub()
	if err != nil {
		return nil, err
	}
	for {
		t := p.cur()
		if t.Kind != TIdent && t.Kind != TEqEq && t.Kind != TNotEq &&
			t.Kind != TLt && t.Kind != TGt && t.Kind != TLtEq && t.Kind != TGtEq &&
			t.Kind != TPlus && t.Kind != TMinus && t.Kind != TStar && t.Kind != TSlash &&
			t.Kind != TPercent && t.Kind != TAndAnd && t.Kind != TOrOr {
			break
		}
		op := t.Lit
		if !ops[op] {
			break
		}
		p.advanceTok()
		right, err := sub()
		if err != nil {
			return nil, err
		}
		left = &BinaryExpr{Op: op, X: left, Y: right, Pos: t.Pos}
	}
	return left, nil
}

func (p *parser) parseOr() (Expr, error) {
	return p.binaryLoop(p.parseAnd, map[string]bool{"||": true})
}

func (p *parser) parseAnd() (Expr, error) {
	return p.binaryLoop(p.parseEq, map[string]bool{"&&": true})
}

func (p *parser) parseEq() (Expr, error) {
	return p.binaryLoop(p.parseRel, map[string]bool{"==": true, "!=": true})
}

func (p *parser) parseRel() (Expr, error) {
	return p.binaryLoop(p.parseAdd, map[string]bool{"<": true, ">": true, "<=": true, ">=": true})
}

func (p *parser) parseAdd() (Expr, error) {
	return p.binaryLoop(p.parseMul, map[string]bool{"+": true, "-": true})
}

func (p *parser) parseMul() (Expr, error) {
	return p.binaryLoop(p.parseUnary, map[string]bool{"*": true, "/": true, "%": true})
}

func (p *parser) parseUnary() (Expr, error) {
	t := p.cur()
	if t.Kind == TMinus || t.Kind == TNot {
		p.advanceTok()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &UnaryExpr{Op: t.Lit, X: x, Pos: t.Pos}, nil
	}
	return p.parsePrimary()
}

func (p *parser) parsePrimary() (Expr, error) {
	t := p.cur()
	switch t.Kind {
	case TInt:
		p.advanceTok()
		var v int64
		var err error
		v, err = parseIntLit(t.Lit)
		if err != nil {
			return nil, errcat.New(errcat.Syntax, "invalid integer literal %q at %s: %v", t.Lit, t.Pos, err)
		}
		return &IntLit{Value: v, Raw: t.Lit, Pos: t.Pos}, nil
	case TIdent:
		switch t.Lit {
		case "true", "false":
			p.advanceTok()
			return &BoolLit{Value: t.Lit == "true", Pos: t.Pos}, nil
		}
		p.advanceTok()
		if p.at(TLParen) {
			args, err := p.parseArgs()
			if err != nil {
				return nil, err
			}
			return &CallExpr{Callee: t.Lit, Args: args, Pos: t.Pos}, nil
		}
		return &Ident{Name: t.Lit, Pos: t.Pos}, nil
	case TLParen:
		p.advanceTok()
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(TRParen, "')'"); err != nil {
			return nil, err
		}
		return e, nil
	case TMinus:
		// handled in unary; defensive
		return nil, errcat.New(errcat.Syntax, "unexpected '-' at %s", t.Pos)
	default:
		return nil, errcat.New(errcat.Syntax, "unexpected token %q at %s", t.Lit, t.Pos)
	}
}

func (p *parser) parseArgs() ([]Expr, error) {
	if _, err := p.expect(TLParen, "'('"); err != nil {
		return nil, err
	}
	var args []Expr
	if !p.at(TRParen) {
		for {
			a, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			args = append(args, a)
			if p.at(TComma) {
				p.advanceTok()
				continue
			}
			break
		}
	}
	if _, err := p.expect(TRParen, "')'"); err != nil {
		return nil, err
	}
	return args, nil
}

func parseIntLit(raw string) (int64, error) {
	return strconv.ParseInt(raw, 0, 64)
}
