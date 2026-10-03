package frontend

import (
	"fmt"
	"strconv"
)

// ParseError is a syntax error.
type ParseError struct {
	Line int
	Col  int
	Msg  string
}

func (e *ParseError) Error() string {
	return fmt.Sprintf("parse error at line %d col %d: %s", e.Line, e.Col, e.Msg)
}

type parser struct {
	toks []Token
	pos  int
}

// Parse compiles source text into a Program.
func Parse(src string) (*Program, error) {
	toks, err := lex(src)
	if err != nil {
		return nil, err
	}
	p := &parser{toks: toks}
	return p.parseProgram()
}

func (p *parser) cur() Token { return p.toks[p.pos] }
func (p *parser) peek() Kind { return p.toks[p.pos].Kind }

func (p *parser) advance() Token {
	t := p.toks[p.pos]
	if p.pos < len(p.toks)-1 {
		p.pos++
	}
	return t
}

func (p *parser) expect(k Kind) (Token, error) {
	if p.peek() != k {
		t := p.cur()
		return t, &ParseError{Line: t.Line, Col: t.Col, Msg: fmt.Sprintf("expected %s, got %s", k, t.Kind)}
	}
	return p.advance(), nil
}

func (p *parser) parseProgram() (*Program, error) {
	prog := &Program{}
	if _, err := p.expect(TPackage); err != nil {
		return nil, err
	}
	name, err := p.expect(TIdent)
	if err != nil {
		return nil, err
	}
	prog.Module = name.Lit

	sawDecl := false
	for p.peek() != TEOF {
		if p.peek() == TImport {
			if sawDecl {
				t := p.cur()
				return nil, &ParseError{Line: t.Line, Col: t.Col, Msg: "import must precede declarations"}
			}
			p.advance()
			m, err := p.expect(TIdent)
			if err != nil {
				return nil, err
			}
			prog.Imports = append(prog.Imports, m.Lit)
			continue
		}
		sensitive := false
		if p.peek() == TSensitive {
			p.advance()
			sensitive = true
		}
		c, err := p.parseClause(sensitive)
		if err != nil {
			return nil, err
		}
		prog.Clauses = append(prog.Clauses, c)
		sawDecl = true
	}
	return prog, nil
}

func (p *parser) parseClause(sensitive bool) (Clause, error) {
	t := p.cur()
	c := Clause{Line: t.Line, Sensitive: sensitive}
	switch p.peek() {
	case TConst:
		if !sensitive {
			// const may optionally be marked sensitive; nothing else changes.
		}
		d, err := p.parseConst()
		if err != nil {
			return c, err
		}
		c.Const = d
	case TType:
		if sensitive {
			return c, &ParseError{Line: t.Line, Col: t.Col, Msg: "@sensitive may only annotate const"}
		}
		d, err := p.parseType()
		if err != nil {
			return c, err
		}
		c.Type = d
	case TFn:
		if sensitive {
			return c, &ParseError{Line: t.Line, Col: t.Col, Msg: "@sensitive may only annotate const"}
		}
		d, err := p.parseFunc()
		if err != nil {
			return c, err
		}
		c.Func = d
	default:
		return c, &ParseError{Line: t.Line, Col: t.Col, Msg: fmt.Sprintf("expected declaration, got %s", t.Kind)}
	}
	return c, nil
}

func (p *parser) parseConst() (*ConstDecl, error) {
	p.advance() // const
	name, err := p.expect(TIdent)
	if err != nil {
		return nil, err
	}
	var tr TypeRef
	// optional declared type: identifier that is not the following "=" assignment
	if p.peek() != TAssign {
		tr, err = p.parseTypeRef()
		if err != nil {
			return nil, err
		}
	}
	if _, err := p.expect(TAssign); err != nil {
		return nil, err
	}
	init, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	return &ConstDecl{Name: name.Lit, Type: tr, Init: init}, nil
}

func (p *parser) parseType() (*TypeDecl, error) {
	p.advance() // type
	name, err := p.expect(TIdent)
	if err != nil {
		return nil, err
	}
	base, err := p.parseTypeRef()
	if err != nil {
		return nil, err
	}
	return &TypeDecl{Name: name.Lit, Base: base}, nil
}

func (p *parser) parseTypeRef() (TypeRef, error) {
	first, err := p.expect(TIdent)
	if err != nil {
		return TypeRef{}, err
	}
	tr := TypeRef{Name: first.Lit}
	if p.peek() == TQual {
		p.advance()
		second, err := p.expect(TIdent)
		if err != nil {
			return TypeRef{}, err
		}
		tr.Module = tr.Name
		tr.Name = second.Lit
	}
	return tr, nil
}

func (p *parser) parseFunc() (*FuncDecl, error) {
	p.advance() // fn
	name, err := p.expect(TIdent)
	if err != nil {
		return nil, err
	}
	d := &FuncDecl{Name: name.Lit}
	if p.peek() == TLBrack {
		p.advance()
		for {
			tp, err := p.expect(TIdent)
			if err != nil {
				return nil, err
			}
			d.TypeParams = append(d.TypeParams, tp.Lit)
			if p.peek() == TComma {
				p.advance()
				continue
			}
			break
		}
		if _, err := p.expect(TRBrack); err != nil {
			return nil, err
		}
	}
	if _, err := p.expect(TLParen); err != nil {
		return nil, err
	}
	for p.peek() != TRParen {
		pn, err := p.expect(TIdent)
		if err != nil {
			return nil, err
		}
		if _, err := p.expect(TColon); err != nil {
			return nil, err
		}
		pt, err := p.parseTypeRef()
		if err != nil {
			return nil, err
		}
		d.Params = append(d.Params, Param{Name: pn.Lit, Type: pt})
		if p.peek() == TComma {
			p.advance()
		}
	}
	p.advance() // )
	if p.peek() == TColon {
		p.advance()
		res, err := p.parseTypeRef()
		if err != nil {
			return nil, err
		}
		d.Result = res
	}
	if _, err := p.expect(TLBrace); err != nil {
		return nil, err
	}
	for p.peek() != TRBrace {
		s, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		d.Body = append(d.Body, s)
	}
	p.advance() // }
	return d, nil
}

func (p *parser) parseStmt() (Stmt, error) {
	t := p.cur()
	s := Stmt{Line: t.Line}
	switch p.peek() {
	case TVar:
		p.advance()
		name, err := p.expect(TIdent)
		if err != nil {
			return s, err
		}
		var tr TypeRef
		if p.peek() == TColon {
			p.advance()
			tr, err = p.parseTypeRef()
			if err != nil {
				return s, err
			}
		}
		var init *Expr
		if p.peek() == TAssign {
			p.advance()
			e, perr := p.parseExpr()
			if perr != nil {
				return s, perr
			}
			init = &e
		}
		s.Var = &VarStmt{Name: name.Lit, Type: tr, Init: init, HasInit: init != nil}
	case TReturn:
		p.advance()
		r := &ReturnStmt{}
		if !p.startsStmt() {
			v, err := p.parseExpr()
			if err != nil {
				return s, err
			}
			r.Value = &v
		}
		s.Return = r
	case TIf:
		p.advance()
		cond, err := p.parseExpr()
		if err != nil {
			return s, err
		}
		if _, err := p.expect(TLBrace); err != nil {
			return s, err
		}
		var then []Stmt
		for p.peek() != TRBrace {
			st, err := p.parseStmt()
			if err != nil {
				return s, err
			}
			then = append(then, st)
		}
		p.advance()
		var els []Stmt
		if p.peek() == TEElse {
			p.advance()
			if p.peek() == TIf {
				st, err := p.parseStmt()
				if err != nil {
					return s, err
				}
				els = []Stmt{st}
			} else {
				if _, err := p.expect(TLBrace); err != nil {
					return s, err
				}
				for p.peek() != TRBrace {
					st, err := p.parseStmt()
					if err != nil {
						return s, err
					}
					els = append(els, st)
				}
				p.advance()
			}
		}
		s.If = &IfStmt{Cond: cond, Then: then, Else: els}
	default:
		v, err := p.parseExpr()
		if err != nil {
			return s, err
		}
		s.Expr = ExprStmt{Value: v}
	}
	return s, nil
}

func (p *parser) startsStmt() bool {
	switch p.peek() {
	case TRBrace, TEOF:
		return true
	}
	return false
}

// Expression parsing: precedence climbing.
func (p *parser) parseExpr() (Expr, error) { return p.parseBinary(0) }

var prec = map[string]int{
	"||": 1, "&&": 2,
	"==": 3, "!=": 3,
	"<": 4, "<=": 4, ">": 4, ">=": 4,
	"+": 5, "-": 5,
	"*": 6, "/": 6,
}

func (p *parser) parseBinary(minPrec int) (Expr, error) {
	t := p.cur()
	left, err := p.parseUnary()
	if err != nil {
		return Expr{}, err
	}
	for {
		op := p.binaryOp()
		if op == "" || prec[op] < minPrec {
			break
		}
		p.advance()
		right, err := p.parseBinary(prec[op] + 1)
		if err != nil {
			return Expr{}, err
		}
		left = Expr{Line: t.Line, Binary: &BinaryExpr{Op: op, Left: left, Right: right}}
	}
	return left, nil
}

func (p *parser) binaryOp() string {
	switch p.peek() {
	case TPlus:
		return "+"
	case TMinus:
		return "-"
	case TStar:
		return "*"
	case TSlash:
		return "/"
	case TEq:
		return "=="
	case TNeq:
		return "!="
	case TLt:
		return "<"
	case TLe:
		return "<="
	case TGt:
		return ">"
	case TGe:
		return ">="
	default:
		return ""
	}
}

func (p *parser) parseUnary() (Expr, error) {
	t := p.cur()
	if p.peek() == TMinus {
		p.advance()
		inner, err := p.parseUnary()
		if err != nil {
			return Expr{}, err
		}
		return Expr{Line: t.Line, Unary: &UnaryExpr{Op: "-", Inner: inner}}, nil
	}
	return p.parsePrimary()
}

func (p *parser) parsePrimary() (Expr, error) {
	t := p.cur()
	switch p.peek() {
	case TInt:
		p.advance()
		v, err := strconv.ParseInt(t.Lit, 10, 64)
		if err != nil {
			return Expr{}, &ParseError{Line: t.Line, Col: t.Col, Msg: "bad integer: " + t.Lit}
		}
		return Expr{Line: t.Line, Lit: &Literal{Kind: LitInt, IntVal: v}}, nil
	case TStr:
		p.advance()
		return Expr{Line: t.Line, Lit: &Literal{Kind: LitStr, StrVal: t.Lit}}, nil
	case TLParen:
		p.advance()
		e, err := p.parseExpr()
		if err != nil {
			return Expr{}, err
		}
		if _, err := p.expect(TRParen); err != nil {
			return Expr{}, err
		}
		return e, nil
	case TIdent:
		p.advance()
		ref := Variable{Name: t.Lit}
		if p.peek() == TQual {
			p.advance()
			second, err := p.expect(TIdent)
			if err != nil {
				return Expr{}, err
			}
			ref.Module = ref.Name
			ref.Name = second.Lit
		}
		if p.peek() == TLBrack {
			p.advance()
			var args []TypeRef
			for {
				tr, err := p.parseTypeRef()
				if err != nil {
					return Expr{}, err
				}
				args = append(args, tr)
				if p.peek() == TComma {
					p.advance()
					continue
				}
				break
			}
			if _, err := p.expect(TRBrack); err != nil {
				return Expr{}, err
			}
			if _, err := p.expect(TLParen); err != nil {
				return Expr{}, err
			}
			ves, err := p.parseArgs()
			if err != nil {
				return Expr{}, err
			}
			return Expr{Line: t.Line, Call: &CallExpr{Module: ref.Module, Name: ref.Name, TypeArgs: args, Args: ves}}, nil
		}
		if p.peek() == TLParen {
			p.advance()
			ves, err := p.parseArgs()
			if err != nil {
				return Expr{}, err
			}
			return Expr{Line: t.Line, Call: &CallExpr{Module: ref.Module, Name: ref.Name, TypeArgs: nil, Args: ves}}, nil
		}
		return Expr{Line: t.Line, Var: &ref}, nil
	default:
		return Expr{}, &ParseError{Line: t.Line, Col: t.Col, Msg: fmt.Sprintf("unexpected %s in expression", t.Kind)}
	}
}

func (p *parser) parseArgs() ([]Expr, error) {
	var args []Expr
	for p.peek() != TRParen {
		e, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		args = append(args, e)
		if p.peek() == TComma {
			p.advance()
		}
	}
	p.advance()
	return args, nil
}
