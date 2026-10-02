package frontend

import "fmt"

// Parse lexes and parses a complete program.
func Parse(src string) (*Program, error) {
	toks, err := lex(src)
	if err != nil {
		return nil, err
	}
	p := &parser{toks: toks}
	return p.parseProgram()
}

type parser struct {
	toks []token
	pos  int
}

func (p *parser) peek() token { return p.toks[p.pos] }

func (p *parser) next() token {
	t := p.toks[p.pos]
	if p.pos < len(p.toks)-1 {
		p.pos++
	}
	return t
}

func (p *parser) isOp(s string) bool {
	t := p.peek()
	return t.kind == tOp && t.val == s
}

func (p *parser) isKw(s string) bool {
	t := p.peek()
	return t.kind == tKeyword && t.val == s
}

func (p *parser) isIdent(s string) bool {
	t := p.peek()
	return t.kind == tIdent && t.val == s
}

func (p *parser) expectOp(s string) (token, error) {
	t := p.peek()
	if t.kind != tOp || t.val != s {
		return t, fmt.Errorf("%s: expected %q, got %q", t.pos, s, t.val)
	}
	return p.next(), nil
}

func (p *parser) expectKw(s string) (token, error) {
	t := p.peek()
	if t.kind != tKeyword || t.val != s {
		return t, fmt.Errorf("%s: expected keyword %q, got %q", t.pos, s, t.val)
	}
	return p.next(), nil
}

func (p *parser) parseProgram() (*Program, error) {
	prog := &Program{}
	for {
		t := p.peek()
		if t.kind == tEOF {
			break
		}
		switch {
		case p.isKw("input"):
			decls, err := p.parseDecls()
			if err != nil {
				return nil, err
			}
			prog.Inputs = append(prog.Inputs, decls...)
		case p.isKw("output"):
			decls, err := p.parseDecls()
			if err != nil {
				return nil, err
			}
			prog.Outputs = append(prog.Outputs, decls...)
		case p.isKw("for"):
			s, r, err := p.parseForOrReduce()
			if err != nil {
				return nil, err
			}
			if r != nil {
				prog.Reduces = append(prog.Reduces, r)
			} else {
				prog.Body = append(prog.Body, s)
			}
		default:
			return nil, fmt.Errorf("%s: expected input/output/for at top level, got %q", t.pos, t.val)
		}
	}
	return prog, nil
}

func (p *parser) parseDecls() ([]*Decl, error) {
	head := p.next() // input/output
	var decls []*Decl
	for {
		nameT := p.peek()
		if nameT.kind != tIdent {
			return nil, fmt.Errorf("%s: expected declaration name, got %q", nameT.pos, nameT.val)
		}
		p.next()
		d := &Decl{Name: nameT.val, Kind: KindScalar}
		if p.isOp("[") {
			p.next()
			length, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			if _, err := p.expectOp("]"); err != nil {
				return nil, err
			}
			d.Kind = KindArray
			d.Length = length
		}
		if _, err := p.expectKw("int64"); err != nil {
			return nil, err
		}
		decls = append(decls, d)
		if p.isOp(",") {
			p.next()
			continue
		}
		_ = head
		p.consumeSemicolon()
		return decls, nil
	}
}

// consumeSemicolon allows an optional ';' but always stops at '}'/EOF/keyword
// boundaries so statements do not require explicit separators.
func (p *parser) consumeSemicolon() {
	if p.isOp(";") {
		p.next()
	}
}

// parseForOrReduce parses a for header, then dispatches on whether the body
// is a single reduce statement.
func (p *parser) parseForOrReduce() (*ForStmt, *ReduceStmt, error) {
	kw, _ := p.expectKw("for")
	varT := p.peek()
	if varT.kind != tIdent {
		return nil, nil, fmt.Errorf("%s: expected loop variable, got %q", varT.pos, varT.val)
	}
	p.next()
	if _, err := p.expectOp(":="); err != nil {
		return nil, nil, err
	}
	begin, err := p.parseExpr()
	if err != nil {
		return nil, nil, err
	}
	if _, err := p.expectOp(".."); err != nil {
		return nil, nil, err
	}
	end, err := p.parseExpr()
	if err != nil {
		return nil, nil, err
	}
	if _, err := p.expectOp("{"); err != nil {
		return nil, nil, err
	}
	if p.isKw("reduce") {
		r, err := p.parseReduceBody(kw, varT, begin, end)
		return nil, r, err
	}
	var stmts []Stmt
	for {
		if p.isOp("}") {
			p.next()
			return &ForStmt{Var: varT.val, Begin: begin, End: end, Pos: kw.pos, Body: stmts}, nil, nil
		}
		if p.peek().kind == tEOF {
			return nil, nil, fmt.Errorf("%s: unexpected EOF in block", p.peek().pos)
		}
		s, err := p.parseStmt()
		if err != nil {
			return nil, nil, err
		}
		stmts = append(stmts, s)
	}
}

func (p *parser) parseReduceBody(kw, varT token, begin, end Expr) (*ReduceStmt, error) {
	if _, err := p.expectKw("reduce"); err != nil {
		return nil, err
	}
	targetT := p.peek()
	if targetT.kind != tIdent {
		return nil, fmt.Errorf("%s: expected reduce target, got %q", targetT.pos, targetT.val)
	}
	p.next()
	var op string
	switch {
	case p.isOp("+"):
		p.next()
		if _, err := p.expectOp("="); err != nil {
			return nil, err
		}
		op = "+"
	case p.isOp("*"):
		p.next()
		if _, err := p.expectOp("="); err != nil {
			return nil, err
		}
		op = "*"
	case p.isKw("concat") || (p.peek().kind == tIdent && p.peek().val == "concat"):
		p.next()
		op = "concat"
		if _, err := p.expectOp("="); err != nil {
			return nil, err
		}
	default:
		t := p.peek()
		return nil, fmt.Errorf("%s: expected reduce op (+=, *=, concat=), got %q", t.pos, t.val)
	}
	srcT := p.peek()
	if srcT.kind != tIdent {
		return nil, fmt.Errorf("%s: expected reduce source array, got %q", srcT.pos, srcT.val)
	}
	p.next()
	var srcIdx Expr
	if _, err := p.expectOp("["); err != nil {
		return nil, err
	}
	var err error
	srcIdx, err = p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expectOp("]"); err != nil {
		return nil, err
	}
	if p.isOp(";") {
		p.next()
	}
	if _, err := p.expectOp("}"); err != nil {
		return nil, err
	}
	return &ReduceStmt{
		LoopVar: varT.val, Begin: begin, End: end, Pos: kw.pos,
		Target: targetT.val, Op: op, Source: srcT.val, SrcIdx: srcIdx,
	}, nil
}

func (p *parser) parseBlockStmt() ([]Stmt, error) {
	if _, err := p.expectOp("{"); err != nil {
		return nil, err
	}
	var stmts []Stmt
	for {
		if p.isOp("}") {
			p.next()
			return stmts, nil
		}
		if p.peek().kind == tEOF {
			return nil, fmt.Errorf("%s: unexpected EOF in block", p.peek().pos)
		}
		s, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		stmts = append(stmts, s)
	}
}

func (p *parser) parseStmt() (Stmt, error) {
	switch {
	case p.isKw("if"):
		return p.parseIf()
	case p.peek().kind == tIdent:
		return p.parseAssign()
	default:
		t := p.peek()
		return nil, fmt.Errorf("%s: expected if or assignment, got %q", t.pos, t.val)
	}
}

func (p *parser) parseAssign() (*AssignStmt, error) {
	nameT := p.next()
	a := &AssignStmt{Name: nameT.val, Pos: nameT.pos}
	if p.isOp("[") {
		p.next()
		idx, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if _, err := p.expectOp("]"); err != nil {
			return nil, err
		}
		a.Idx = idx
	}
	if _, err := p.expectOp("="); err != nil {
		return nil, err
	}
	rhs, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	a.Rhs = rhs
	p.consumeSemicolon()
	return a, nil
}

func (p *parser) parseIf() (*IfStmt, error) {
	kw, _ := p.expectKw("if")
	if _, err := p.expectOp("("); err != nil {
		return nil, err
	}
	cond, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expectOp(")"); err != nil {
		return nil, err
	}
	then, err := p.parseBlockStmt()
	if err != nil {
		return nil, err
	}
	s := &IfStmt{Cond: cond, Pos: kw.pos, Then: then}
	if p.isKw("else") {
		p.next()
		switch {
		case p.isKw("if"):
			nested, err := p.parseIf()
			if err != nil {
				return nil, err
			}
			s.Else = []Stmt{nested}
		case p.isOp("{"):
			els, err := p.parseBlockStmt()
			if err != nil {
				return nil, err
			}
			s.Else = els
		default:
			t := p.peek()
			return nil, fmt.Errorf("%s: expected block after else, got %q", t.pos, t.val)
		}
	}
	return s, nil
}
