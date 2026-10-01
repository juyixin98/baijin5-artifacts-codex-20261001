package ir

import (
	"genstatemachine/internal/frontend"
	"genstatemachine/internal/gerr"
)

// tryStmt lowers structured try/catch/finally into explicit handler frames
// and dedicated catch/finally/after blocks.
func (c *compiler) tryStmt(x *frontend.TryStmt, b *Block) (*Block, error) {
	if len(x.Catches) > 1 {
		return nil, gerr.New(gerr.EValidate,
			"at most one catch clause per try is supported").AtPos(x.Line, 0)
	}

	bodyB := c.newBlock("trybody")
	catchB := c.newBlock("catch")
	var finB, after *Block
	if len(x.Finally) > 0 {
		finB = c.newBlock("finally")
	}
	after = c.newBlock("tryafter")

	if b.Term.Kind == -1 {
		b.Term = Term{Kind: TermJump, Target: c.blockIndex(bodyB)}
	}

	idxCatch := c.blockIndex(catchB)
	idxFin := -1
	if finB != nil {
		idxFin = c.blockIndex(finB)
	}
	idxAfter := c.blockIndex(after)

	h := Handler{FinallyBlock: -1, CatchBlock: -1, AfterBlock: idxAfter}
	if len(x.Catches) == 1 {
		h.Kind = HandlerCatch
		h.CatchBlock = idxCatch
	} else {
		h.Kind = HandlerFinally
	}
	if finB != nil {
		h.FinallyBlock = idxFin
	}

	if len(x.Catches) == 1 && x.Catches[0].Test != nil {
		fr, err := c.expr(bodyB, x.Catches[0].Test)
		if err != nil {
			return nil, err
		}
		h.HasFilter = true
		h.FilterReg = fr
	}
	c.selectBlock(bodyB)
	c.emit(Op{Kind: OpPushHandler, Line: x.Line, Handler: h})

	bodyEnd, err := c.stmts(x.Body, bodyB)
	if err != nil {
		return nil, err
	}
	if c.isLive(bodyEnd) {
		if finB != nil {
			bodyEnd.Term = Term{Kind: TermJump, Target: idxFin}
		} else {
			c.selectBlock(bodyEnd)
			c.emit(Op{Kind: OpPopHandler, Line: x.Line})
			bodyEnd.Term = Term{Kind: TermJump, Target: idxAfter}
		}
	}

	if len(x.Catches) == 1 {
		clause := x.Catches[0]
		c.selectBlock(catchB)
		c.emit(Op{Kind: OpSetCatchMode, Line: clause.Line})
		r := c.alloc()
		c.emit(Op{Kind: OpBindCatch, Line: clause.Line, Name: clause.Name, R: r})
		catchEnd, err := c.stmts(clause.Body, catchB)
		if err != nil {
			return nil, err
		}
		if c.isLive(catchEnd) {
			if finB != nil {
				catchEnd.Term = Term{Kind: TermJump, Target: idxFin}
			} else {
				c.selectBlock(catchEnd)
				c.emit(Op{Kind: OpPopHandler, Line: clause.Line})
				catchEnd.Term = Term{Kind: TermJump, Target: idxAfter}
			}
		}
	} else {
		catchB.Term = Term{Kind: TermJump, Target: idxAfter}
	}

	if finB != nil {
		c.selectBlock(finB)
		c.emit(Op{Kind: OpSetFinallyMode, Line: x.Line})
		finEnd, err := c.stmts(x.Finally, finB)
		if err != nil {
			return nil, err
		}
		if c.isLive(finEnd) {
			c.selectBlock(finEnd)
			c.emit(Op{Kind: OpPopHandler, Line: x.Line})
			c.emit(Op{Kind: OpEndFinally, Line: x.Line})
			finEnd.Term = Term{Kind: TermJump, Target: idxAfter}
		}
	}

	return after, nil
}
