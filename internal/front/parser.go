package front

import (
	"fmt"

	"funcspect/internal/diag"
)

// Parse converts source text into an AST Program.
func Parse(src string) (*Program, error) {
	tokens, err := lex(src)
	if err != nil {
		return nil, err
	}
	p := &parser{tokens: tokens}
	prog := &Program{Funcs: map[string]*FnDecl{}}
	for {
		tok := p.peek()
		if tok.Kind == TEOF {
			break
		}
		fn, err := p.parseFn()
		if err != nil {
			return nil, err
		}
		if _, dup := prog.Funcs[fn.Name]; dup {
			return nil, atErr(tok, diag.CatSyntax, "duplicate function %q", fn.Name)
		}
		prog.Funcs[fn.Name] = fn
		prog.Order = append(prog.Order, fn.Name)
	}
	if len(prog.Order) == 0 {
		return nil, atErr(p.peek(), diag.CatSyntax, "empty program: at least one function is required")
	}
	return prog, nil
}

type parser struct {
	tokens []Token
	pos    int
}

type stopSet map[string]bool

func (p *parser) peek() Token { return p.tokens[p.pos] }
func (p *parser) next() Token { t := p.tokens[p.pos]; p.pos++; return t }

func (p *parser) expectOp(op string) (Token, error) {
	tok := p.next()
	if tok.Kind != TOp || tok.Value != op {
		return tok, atErr(tok, diag.CatSyntax, "expected %q, got %q", op, tok.Value)
	}
	return tok, nil
}

func (p *parser) expectPunct(kind TokenKind, text string) (Token, error) {
	tok := p.next()
	if tok.Kind != kind {
		return tok, atErr(tok, diag.CatSyntax, "expected %q, got %q", text, tok.Value)
	}
	return tok, nil
}

func (p *parser) expectKeyword(kw string) (Token, error) {
	tok := p.next()
	if tok.Kind != TKeyword || tok.Value != kw {
		return tok, atErr(tok, diag.CatSyntax, "expected keyword %q, got %q", kw, tok.Value)
	}
	return tok, nil
}

func (p *parser) expectIdent() (Token, error) {
	tok := p.next()
	if tok.Kind != TIdent {
		return tok, atErr(tok, diag.CatSyntax, "expected identifier, got %q", tok.Value)
	}
	return tok, nil
}

func (p *parser) parseFn() (*FnDecl, error) {
	tok := p.peek()
	fn := &FnDecl{}
	if tok.Kind == TKeyword && (tok.Value == "pure" || tok.Value == "impure") {
		p.next()
		if tok.Value == "pure" {
			fn.Pure = true
		} else {
			fn.ExplicitImpure = true
		}
	}
	nameTok, err := p.expectIdent()
	if err != nil {
		return nil, err
	}
	fn.Name = nameTok.Value
	fn.Node = nodeOf(nameTok)
	if _, err := p.expectPunct(TLParen, "("); err != nil {
		return nil, err
	}
	for p.peek().Kind != TRParen {
		param, err := p.parseParam()
		if err != nil {
			return nil, err
		}
		fn.Params = append(fn.Params, param)
		if p.peek().Kind == TOp && p.peek().Value == "," {
			p.next()
			continue
		}
		break
	}
	if _, err := p.expectPunct(TRParen, ")"); err != nil {
		return nil, err
	}
	if _, err := p.expectOp("="); err != nil {
		return nil, err
	}
	body, err := p.parseExpr(nil)
	if err != nil {
		return nil, err
	}
	fn.Body = body
	return fn, nil
}

func (p *parser) parseParam() (Param, error) {
	nameTok, err := p.expectIdent()
	if err != nil {
		return Param{}, err
	}
	param := Param{Node: nodeOf(nameTok), Name: nameTok.Value, Static: true}
	if p.peek().Kind == TOp && p.peek().Value == "!" {
		p.next()
		param.Static = false
	}
	return param, nil
}

// precedence: || < && < == != < < <= > >= < + - < * / % < unary.
var binaryPrec = map[string]int{
	"||": 1,
	"&&": 2,
	"==": 3, "!=": 3,
	"<": 4, "<=": 4, ">": 4, ">=": 4,
	"+": 5, "-": 5,
	"*": 6, "/": 6, "%": 6,
}

// parseExpr parses one expression. stops (then/else/in) binds looser than any
// binary operator and terminates the current expression.
func (p *parser) parseExpr(stops stopSet) (Expr, error) {
	return p.parseBinary(1, stops)
}

// atStop reports whether the next token is a keyword that ends this expression.
func (p *parser) atStop(stops stopSet) bool {
	t := p.peek()
	return t.Kind == TKeyword && stops[t.Value]
}

// parseBinary is Pratt precedence climbing. The stop set (then/else/in) is
// consulted only at the top precedence level (minPrec == 1). Right-operand
// recursion starts at prec+1 and ignores stops, so `n <= 1 then` parses the
// operand 1 and returns to the outer loop, which then recognizes `then`.
func (p *parser) parseBinary(minPrec int, stops stopSet) (Expr, error) {
	left, err := p.parseUnary()
	if err != nil {
		return nil, err
	}
	for {
		if minPrec == 1 && p.atStop(stops) {
			break
		}
		tok := p.peek()
		if tok.Kind != TOp {
			break
		}
		prec, ok := binaryPrec[tok.Value]
		if !ok || prec < minPrec {
			break
		}
		p.next()
		right, err := p.parseBinary(prec+1, nil)
		if err != nil {
			return nil, err
		}
		left = &Binary{Node: nodeOf(tok), Op: tok.Value, Lhs: left, Rhs: right}
	}
	return left, nil
}

func (p *parser) parseUnary() (Expr, error) {
	tok := p.peek()
	if tok.Kind == TOp && (tok.Value == "-" || tok.Value == "!") {
		p.next()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &Unary{Node: nodeOf(tok), Op: tok.Value, X: x}, nil
	}
	return p.parsePrimary()
}

func (p *parser) parsePrimary() (Expr, error) {
	tok := p.next()
	switch tok.Kind {
	case TInt:
		var v int64
		if _, err := fmt.Sscan(tok.Value, &v); err != nil {
			return nil, atErr(tok, diag.CatSyntax, "invalid integer %q", tok.Value)
		}
		return &IntLit{Node: nodeOf(tok), Value: v}, nil
	case TKeyword:
		switch tok.Value {
		case "true":
			return &BoolLit{Node: nodeOf(tok), Value: true}, nil
		case "false":
			return &BoolLit{Node: nodeOf(tok), Value: false}, nil
		case "if":
			return p.parseIfRest(tok)
		case "let":
			return p.parseLetRest(tok)
		default:
			return nil, atErr(tok, diag.CatSyntax, "unexpected keyword %q", tok.Value)
		}
	case TIdent:
		if p.peek().Kind == TLParen {
			return p.parseCallRest(tok)
		}
		return &VarRef{Node: nodeOf(tok), Name: tok.Value}, nil
	case TLParen:
		e, err := p.parseExpr(nil)
		if err != nil {
			return nil, err
		}
		if _, err := p.expectPunct(TRParen, ")"); err != nil {
			return nil, err
		}
		return e, nil
	default:
		return nil, atErr(tok, diag.CatSyntax, "unexpected token %q", tok.Value)
	}
}

func (p *parser) parseCallRest(head Token) (Expr, error) {
	p.next() // '('
	call := &CallExpr{Node: nodeOf(head), Name: head.Value}
	for p.peek().Kind != TRParen {
		arg, err := p.parseExpr(nil)
		if err != nil {
			return nil, err
		}
		call.Args = append(call.Args, arg)
		if p.peek().Kind == TOp && p.peek().Value == "," {
			p.next()
			continue
		}
		break
	}
	if _, err := p.expectPunct(TRParen, ")"); err != nil {
		return nil, nil
	}
	return call, nil
}

func (p *parser) parseIfRest(ifTok Token) (Expr, error) {
	cond, err := p.parseExpr(stopSet{"then": true})
	if err != nil {
		return nil, err
	}
	if _, err := p.expectKeyword("then"); err != nil {
		return nil, err
	}
	thenE, err := p.parseExpr(stopSet{"else": true})
	if err != nil {
		return nil, err
	}
	if _, err := p.expectKeyword("else"); err != nil {
		return nil, err
	}
	elseE, err := p.parseExpr(nil)
	if err != nil {
		return nil, err
	}
	return &IfExpr{Node: nodeOf(ifTok), Cond: cond, Then: thenE, Else: elseE}, nil
}

func (p *parser) parseLetRest(letTok Token) (Expr, error) {
	nameTok, err := p.expectIdent()
	if err != nil {
		return nil, err
	}
	if _, err := p.expectOp("="); err != nil {
		return nil, err
	}
	bound, err := p.parseExpr(stopSet{"in": true})
	if err != nil {
		return nil, err
	}
	if _, err := p.expectKeyword("in"); err != nil {
		return nil, err
	}
	body, err := p.parseExpr(nil)
	if err != nil {
		return nil, err
	}
	return &LetExpr{Node: nodeOf(letTok), Name: nameTok.Value, Bound: bound, Body: body}, nil
}

func nodeOf(t Token) Node { return Node{Line: t.Line, Col: t.Col} }

func atErr(tok Token, cat diag.Category, format string, args ...any) error {
	return diag.New(cat, fmt.Sprintf(format, args...)).At(tok.Line, tok.Col)
}
