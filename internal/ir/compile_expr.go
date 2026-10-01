package ir

import (
	"genstatemachine/internal/frontend"
	"genstatemachine/internal/gerr"
)

// expr lowers an expression into block b and returns the result register.
func (c *compiler) expr(b *Block, e frontend.Expr) (int, error) {
	c.selectBlock(b)
	switch x := e.(type) {
	case *frontend.IntLit:
		r := c.alloc()
		c.emit(Op{Kind: OpConst, Line: x.Line, R: r, Const: Value{Kind: VInt, I: x.Value}})
		return r, nil
	case *frontend.StrLit:
		r := c.alloc()
		c.emit(Op{Kind: OpConst, Line: x.Line, R: r, Const: Value{Kind: VStr, S: x.Value}})
		return r, nil
	case *frontend.BoolLit:
		r := c.alloc()
		c.emit(Op{Kind: OpConst, Line: x.Line, R: r, Const: Value{Kind: VBool, B: x.Value}})
		return r, nil
	case *frontend.NilLit:
		r := c.alloc()
		c.emit(Op{Kind: OpConst, Line: x.Line, R: r, Const: Value{Kind: VNil}})
		return r, nil
	case *frontend.Ident:
		r := c.alloc()
		c.emit(Op{Kind: OpLoad, Line: x.Line, Name: x.Name, R: r})
		return r, nil
	case *frontend.UnaryExpr:
		inner, err := c.expr(b, x.Expr)
		if err != nil {
			return 0, err
		}
		r := c.alloc()
		c.emit(Op{Kind: OpUnary, Line: x.Line, Op: x.Op, R: r, R1: inner})
		return r, nil
	case *frontend.BinaryExpr:
		lhs, err := c.expr(b, x.LHS)
		if err != nil {
			return 0, err
		}
		rhs, err := c.expr(b, x.RHS)
		if err != nil {
			return 0, err
		}
		r := c.alloc()
		c.emit(Op{Kind: OpBinary, Line: x.Line, Op: x.Op, R: r, R1: lhs, R2: rhs})
		return r, nil
	case *frontend.CallExpr:
		if x.Name != "log" {
			return 0, gerr.New(gerr.EValidate, "unknown builtin %q", x.Name).AtPos(x.Line, 0)
		}
		regs := make([]int, 0, len(x.Args))
		for _, a := range x.Args {
			ar, err := c.expr(b, a)
			if err != nil {
				return 0, err
			}
			regs = append(regs, ar)
		}
		r := c.alloc()
		c.emit(Op{Kind: OpLog, Line: x.Line, R: r, ArgRegs: regs})
		return r, nil
	}
	return 0, gerr.New(gerr.EValidate, "unsupported expression %T", e)
}

func (c *compiler) blockIndex(b *Block) int {
	for i, bb := range c.blocks {
		if bb == b {
			return i
		}
	}
	return -1
}
