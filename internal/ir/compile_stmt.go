package ir

import (
	"genstatemachine/internal/frontend"
	"genstatemachine/internal/gerr"
)

// stmts compiles list into entry block b and returns the live exit block (the
// block receiving subsequent fall-through code), or nil if control cannot
// fall through.
func (c *compiler) stmts(list []frontend.Stmt, b *Block) (*Block, error) {
	cur := b
	for _, s := range list {
		if !c.isLive(cur) {
			return nil, nil
		}
		var err error
		cur, err = c.stmt(s, cur)
		if err != nil {
			return nil, err
		}
	}
	return cur, nil
}

func (c *compiler) stmt(s frontend.Stmt, b *Block) (*Block, error) {
	c.selectBlock(b)
	switch x := s.(type) {
	case *frontend.BlockStmt:
		return c.stmts(x.List, b)

	case *frontend.VarStmt:
		if x.Init != nil {
			r, err := c.expr(b, x.Init)
			if err != nil {
				return nil, err
			}
			c.emit(Op{Kind: OpStore, Line: x.Line, Name: x.Name, R1: r})
		} else {
			r := c.alloc()
			c.emit(Op{Kind: OpConst, Line: x.Line, R: r, Const: Value{Kind: VNil}})
			c.emit(Op{Kind: OpStore, Line: x.Line, Name: x.Name, R1: r})
		}
		return b, nil

	case *frontend.AssignStmt:
		r, err := c.expr(b, x.Expr)
		if err != nil {
			return nil, err
		}
		c.emit(Op{Kind: OpStore, Line: x.Line, Name: x.Name, R1: r})
		return b, nil

	case *frontend.ExprStmt:
		if _, err := c.expr(b, x.Expr); err != nil {
			return nil, err
		}
		return b, nil

	case *frontend.YieldStmt:
		r := -1
		if x.Expr != nil {
			var err error
			r, err = c.expr(b, x.Expr)
			if err != nil {
				return nil, err
			}
		}
		resume := c.newBlock("resume")
		b.Term = Term{Kind: TermYield, Line: x.Line, Reg: r, Target: c.blockIndex(resume)}
		return resume, nil

	case *frontend.ReturnStmt:
		r := -1
		if x.Expr != nil {
			var err error
			r, err = c.expr(b, x.Expr)
			if err != nil {
				return nil, err
			}
		}
		b.Term = Term{Kind: TermReturn, Line: x.Line, Reg: r}
		return nil, nil

	case *frontend.ThrowStmt:
		r, err := c.expr(b, x.Expr)
		if err != nil {
			return nil, err
		}
		b.Term = Term{Kind: TermRaise, Line: x.Line, Reg: r}
		return nil, nil

	case *frontend.IfStmt:
		return c.ifStmt(x, b)

	case *frontend.WhileStmt:
		return c.whileStmt(x, b)

	case *frontend.TryStmt:
		return c.tryStmt(x, b)
	}
	return nil, gerr.New(gerr.EValidate, "unsupported statement %T", s)
}

func (c *compiler) ifStmt(x *frontend.IfStmt, b *Block) (*Block, error) {
	cond, err := c.expr(b, x.Cond)
	if err != nil {
		return nil, err
	}
	thenB := c.newBlock("then")
	elseB := c.newBlock("else")
	merge := c.newBlock("ifmerge")
	b.Term = Term{Kind: TermBranch, Line: x.Line, Reg: cond,
		Target: c.blockIndex(thenB), Other: c.blockIndex(elseB)}

	thenEnd, err := c.stmts(x.Then, thenB)
	if err != nil {
		return nil, err
	}
	if c.isLive(thenEnd) {
		thenEnd.Term = Term{Kind: TermJump, Target: c.blockIndex(merge)}
	}

	elseEnd, err := c.stmts(x.Else, elseB)
	if err != nil {
		return nil, err
	}
	if c.isLive(elseEnd) {
		elseEnd.Term = Term{Kind: TermJump, Target: c.blockIndex(merge)}
	}

	if !c.isLive(thenEnd) && !c.isLive(elseEnd) {
		return nil, nil
	}
	return merge, nil
}

func (c *compiler) whileStmt(x *frontend.WhileStmt, b *Block) (*Block, error) {
	if b.Term.Kind == -1 {
		b.Term = Term{Kind: TermJump, Target: len(c.blocks)}
	}
	head := c.newBlock("whilehead")
	c.selectBlock(head)
	cond, err := c.expr(head, x.Cond)
	if err != nil {
		return nil, err
	}
	body := c.newBlock("whilebody")
	end := c.newBlock("whileend")
	head.Term = Term{Kind: TermBranch, Line: x.Line, Reg: cond,
		Target: c.blockIndex(body), Other: c.blockIndex(end)}

	bodyEnd, err := c.stmts(x.Body, body)
	if err != nil {
		return nil, err
	}
	if c.isLive(bodyEnd) {
		bodyEnd.Term = Term{Kind: TermJump, Target: c.blockIndex(head)}
	}
	return end, nil
}
