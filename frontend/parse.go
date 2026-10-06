package frontend

import "strconv"

// Parse lexes, parses and validates a match program.
//
// Grammar:
//
//	program  := ctorDecl* "match" IDENT "{" branch* "}" EOF
//	ctorDecl := "ctor" IDENT INT
//	branch   := "|" pattern ("if" expr)? "=>" STRING
//	pattern  := "_" | IDENT | literal | CTOR ("(" pattern ("," pattern)* ")")?
//	expr     := orExpr
//	orExpr   := andExpr ("or" andExpr)*
//	andExpr  := unary ("and" unary)*
//	unary    := "not" unary | primary
//	primary  := literal | IDENT | IDENT "(" expr ("," expr)* ")" | "(" expr ")"
//	literal  := INT | STRING | "true" | "false"
//
// Constructors start with an uppercase letter; variables with a lowercase one.
func Parse(src string) (*Program, error) {
	toks, lexErr := lex(src)
	if lexErr != nil {
		return nil, lexErr
	}
	p := &parser{toks: toks}
	prog, perr := p.program()
	if perr != nil {
		return nil, perr
	}
	prog.Source = src
	if verr := validate(prog); verr != nil {
		return nil, verr
	}
	return prog, nil
}

type parser struct {
	toks []token
	i    int
}

func (p *parser) peek() token { return p.toks[p.i] }

func (p *parser) next() token {
	t := p.toks[p.i]
	if t.kind != tEOF {
		p.i++
	}
	return t
}

func (p *parser) expect(k tokKind, what string) (token, *Error) {
	t := p.next()
	if t.kind != k {
		return t, errorf(CatParse, t.pos, "expected %s, got %s", what, t.describe())
	}
	return t, nil
}

func (p *parser) atKw(kw string) bool {
	return p.peek().kind == tIdent && p.peek().text == kw
}

func (p *parser) program() (*Program, *Error) {
	prog := &Program{Ctors: map[string]int{}}
	for p.atKw("ctor") {
		p.next()
		name := p.next()
		if name.kind != tIdent {
			return nil, errorf(CatParse, name.pos, "expected constructor name, got %s", name.describe())
		}
		if name.text[0] < 'A' || name.text[0] > 'Z' {
			return nil, errorf(CatSemantic, name.pos, "constructor %q must start with an uppercase letter", name.text)
		}
		ar := p.next()
		if ar.kind != tInt {
			return nil, errorf(CatParse, ar.pos, "expected arity for constructor %q, got %s", name.text, ar.describe())
		}
		arity, _ := strconv.ParseInt(ar.text, 10, 64)
		if arity < 0 || arity > 16 {
			return nil, errorf(CatSemantic, ar.pos, "constructor arity %d out of range (0..16)", arity)
		}
		if _, dup := prog.Ctors[name.text]; dup {
			return nil, errorf(CatSemantic, name.pos, "duplicate constructor %q", name.text)
		}
		prog.Ctors[name.text] = int(arity)
		prog.CtorOrder = append(prog.CtorOrder, name.text)
	}
	if !p.atKw("match") {
		t := p.peek()
		return nil, errorf(CatParse, t.pos, "expected 'match', got %s", t.describe())
	}
	p.next()
	scr := p.next()
	if scr.kind != tIdent {
		return nil, errorf(CatParse, scr.pos, "expected scrutinee name, got %s", scr.describe())
	}
	prog.Scrutinee = scr.text
	if _, err := p.expect(tLBrace, "'{'"); err != nil {
		return nil, err
	}
	for p.peek().kind == tPipe {
		br, err := p.branch(len(prog.Branches))
		if err != nil {
			return nil, err
		}
		prog.Branches = append(prog.Branches, br)
	}
	if _, err := p.expect(tRBrace, "'}'"); err != nil {
		return nil, err
	}
	if t := p.next(); t.kind != tEOF {
		return nil, errorf(CatParse, t.pos, "unexpected %s after match expression", t.describe())
	}
	if len(prog.Branches) == 0 {
		return nil, errorf(CatParse, Pos{Line: 1, Col: 1}, "match expression must have at least one branch")
	}
	return prog, nil
}

func (p *parser) branch(idx int) (Branch, *Error) {
	bar := p.next() // consume '|'
	pat, err := p.pattern()
	if err != nil {
		return Branch{}, err
	}
	b := Branch{Index: idx, Pat: pat, P: bar.pos}
	if p.atKw("if") {
		p.next()
		g, err := p.expr()
		if err != nil {
			return Branch{}, err
		}
		b.Guard = g
	}
	if _, err := p.expect(tArrow, "'=>'"); err != nil {
		return Branch{}, err
	}
	lbl := p.next()
	if lbl.kind != tString {
		return Branch{}, errorf(CatParse, lbl.pos, "expected string label after '=>', got %s", lbl.describe())
	}
	b.Label = lbl.text
	return b, nil
}

func (p *parser) pattern() (Pattern, *Error) {
	t := p.next()
	switch t.kind {
	case tWildcard:
		return PWildcard{P: t.pos}, nil
	case tInt:
		v, convErr := strconv.ParseInt(t.text, 10, 64)
		if convErr != nil {
			return nil, errorf(CatParse, t.pos, "invalid integer %q", t.text)
		}
		return PLit{Val: IntLit(v), P: t.pos}, nil
	case tString:
		return PLit{Val: StrLit(t.text), P: t.pos}, nil
	case tIdent:
		switch t.text {
		case "true":
			return PLit{Val: BoolLit(true), P: t.pos}, nil
		case "false":
			return PLit{Val: BoolLit(false), P: t.pos}, nil
		}
		if t.text[0] >= 'A' && t.text[0] <= 'Z' {
			ctor := &PCtor{Name: t.text, P: t.pos}
			if p.peek().kind == tLParen {
				p.next()
				for {
					sub, err := p.pattern()
					if err != nil {
						return nil, err
					}
					ctor.Args = append(ctor.Args, sub)
					if p.peek().kind == tComma {
						p.next()
						continue
					}
					break
				}
				if _, err := p.expect(tRParen, "')'"); err != nil {
					return nil, err
				}
			}
			return *ctor, nil
		}
		return PVar{Name: t.text, P: t.pos}, nil
	default:
		return nil, errorf(CatParse, t.pos, "expected pattern, got %s", t.describe())
	}
}

func (p *parser) expr() (Expr, *Error) { return p.orExpr() }

func (p *parser) orExpr() (Expr, *Error) {
	l, err := p.andExpr()
	if err != nil {
		return nil, err
	}
	for p.atKw("or") {
		op := p.next()
		r, err := p.andExpr()
		if err != nil {
			return nil, err
		}
		l = EOr{L: l, R: r, P: op.pos}
	}
	return l, nil
}

func (p *parser) andExpr() (Expr, *Error) {
	l, err := p.unary()
	if err != nil {
		return nil, err
	}
	for p.atKw("and") {
		op := p.next()
		r, err := p.unary()
		if err != nil {
			return nil, err
		}
		l = EAnd{L: l, R: r, P: op.pos}
	}
	return l, nil
}

func (p *parser) unary() (Expr, *Error) {
	if p.atKw("not") {
		op := p.next()
		e, err := p.unary()
		if err != nil {
			return nil, err
		}
		return ENot{E: e, P: op.pos}, nil
	}
	return p.primary()
}

func (p *parser) primary() (Expr, *Error) {
	t := p.next()
	switch t.kind {
	case tInt:
		v, convErr := strconv.ParseInt(t.text, 10, 64)
		if convErr != nil {
			return nil, errorf(CatParse, t.pos, "invalid integer %q", t.text)
		}
		return ELit{Val: IntLit(v), P: t.pos}, nil
	case tString:
		return ELit{Val: StrLit(t.text), P: t.pos}, nil
	case tLParen:
		e, err := p.expr()
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(tRParen, "')'"); err != nil {
			return nil, err
		}
		return e, nil
	case tIdent:
		switch t.text {
		case "true":
			return ELit{Val: BoolLit(true), P: t.pos}, nil
		case "false":
			return ELit{Val: BoolLit(false), P: t.pos}, nil
		}
		if p.peek().kind == tLParen {
			p.next()
			var args []Expr
			if p.peek().kind != tRParen {
				for {
					a, err := p.expr()
					if err != nil {
						return nil, err
					}
					args = append(args, a)
					if p.peek().kind == tComma {
						p.next()
						continue
					}
					break
				}
			}
			if _, err := p.expect(tRParen, "')'"); err != nil {
				return nil, err
			}
			return ECall{Func: t.text, Args: args, P: t.pos}, nil
		}
		return EVar{Name: t.text, P: t.pos}, nil
	default:
		return nil, errorf(CatParse, t.pos, "expected expression, got %s", t.describe())
	}
}
