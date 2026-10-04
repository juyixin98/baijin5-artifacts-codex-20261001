package ir

import (
	"fmt"

	"genfsm/internal/ast"
)

func (c *comp) genExpr(e ast.Expr) error {
	switch n := e.(type) {
	case *ast.NullLit:
		c.emitAt(n.P, Op{C: OConstNull})
	case *ast.BoolLit:
		c.emitAt(n.P, Op{C: OConstBool, B: n.Value, I: c.boolLit(n.Value)})
	case *ast.IntLit:
		c.emitAt(n.P, Op{C: OConstInt, I: c.intLit(n.Value)})
	case *ast.StrLit:
		c.emitAt(n.P, Op{C: OConstStr, I: c.strLit(n.Value)})
	case *ast.NameExpr:
		c.emitAt(n.P, Op{C: OLoad, I: int64(c.local(n.Name))})
	case *ast.UnaryExpr:
		if err := c.genExpr(n.X); err != nil {
			return err
		}
		c.emitAt(n.P, Op{C: OUnary, S: n.Op})
	case *ast.BinaryExpr:
		if n.ShortCircuit {
			return c.genShortCircuit(n)
		}
		if err := c.genExpr(n.X); err != nil {
			return err
		}
		if err := c.genExpr(n.Y); err != nil {
			return err
		}
		c.emitAt(n.P, Op{C: OBinary, S: n.Op})
	case *ast.CallExpr:
		for _, a := range n.Args {
			if err := c.genExpr(a); err != nil {
				return err
			}
		}
		c.emitAt(n.P, Op{C: OCall, S: n.Callee, I: int64(len(n.Args))})
	case *ast.YieldExpr:
		if n.Init != nil {
			if err := c.genExpr(n.Init); err != nil {
				return err
			}
		} else {
			c.emitAt(n.P, Op{C: OConstNull})
		}
		pc := len(c.r.Code)
		handlerN := 0 // filled at resolve? handlers are dynamic; tracked at VM time
		_ = handlerN
		c.emitAt(n.P, Op{C: OYield})
		c.pauseID++
		c.r.Pauses = append(c.r.Pauses, PausePoint{ID: c.pauseID, PC: pc, Line: n.P.Line, Col: n.P.Col, NLocals: c.r.NSlots})
	default:
		return fmt.Errorf("ir: unsupported expression %T", e)
	}
	return nil
}

func (c *comp) genShortCircuit(n *ast.BinaryExpr) error {
	if err := c.genExpr(n.X); err != nil {
		return err
	}
	end := c.newLabel()
	rhs := c.newLabel()
	if n.Op == "&&" {
		// if !x jump end (leave x on stack as result)
		c.emitAt(n.P, Op{C: OBranchFalse, L1: end})
	} else {
		// if x jump end (truthy)
		c.emitAt(n.P, Op{C: OBranchTrue, L1: end})
	}
	c.emit(Op{C: OPop})
	if err := c.genExpr(n.Y); err != nil {
		return err
	}
	c.emit(Op{C: OJump, L1: end})
	c.mark(rhs)
	c.mark(end)
	return nil
}

// genFor compiles for v in g() { body }
//
//	OSetupIter end         (pushes iter handler holding gen slot)
//	start:
//	  OIterNext doneSlot, doneLbl   (calls g.Next(); pushes value on value or jumps)
//	  store var
//	  body
//	  jump start
//	done:
//	  OPopIter              (normal path: nothing to clean, pop handler)
//	end:
func (c *comp) genFor(n *ast.ForStmt) error {
	if err := c.genExpr(n.Iter); err != nil {
		return err
	}
	genSlot := c.alloc()
	c.emit(Op{C: OStore, I: int64(genSlot)})
	start := c.newLabel()
	done := c.newLabel()
	emitPos := n.P
	c.emitAt(emitPos, Op{C: OSetupIter, I: int64(genSlot)})
	c.mark(start)
	c.emitAt(emitPos, Op{C: OIterNext, L1: done, I: int64(genSlot)})
	c.pushScope()
	varSlot := c.declare(n.Var)
	c.emit(Op{C: OStore, I: int64(varSlot)})
	if err := c.genBlockIn(n.Body); err != nil {
		c.popScope()
		return err
	}
	c.popScope()
	c.emit(Op{C: OJump, L1: start})
	c.mark(done)
	c.emit(Op{C: OPopIter})
	return nil
}

// genTry compiles a structured try/catch/finally using an explicit handler
// stack and OEnterFinally/OEndFinally continuation protocol. See README.
func (c *comp) genTry(n *ast.TryStmt) error {
	var catchLbl, finLbl Label
	if n.Catch != nil {
		catchLbl = c.newLabel()
	}
	if n.Finally != nil {
		finLbl = c.newLabel()
	}
	endLbl := c.newLabel()

	c.emitAt(n.P, Op{C: OSetup, L1: catchLbl, L2: finLbl})
	if err := c.genBlock(n.Body); err != nil {
		return err
	}
	// Normal completion of try body: pop handler, run finally, continue.
	c.emit(Op{C: OPopHandler})
	if n.Finally != nil {
		c.emit(Op{C: OEnterFinally, L1: endLbl})
		if err := c.genBlock(n.Finally); err != nil {
			return err
		}
		c.emit(Op{C: OEndFinally})
	}
	c.emit(Op{C: OJump, L1: endLbl})

	if n.Catch != nil {
		c.mark(catchLbl)
		// OBindCatch pops the try handler; stack: exception
		c.pushScope()
		paramSlot := c.declare(n.Catch.Param)
		c.emitAt(n.Catch.P, Op{C: OBindCatch, I: int64(paramSlot)})
		if err := c.genBlockIn(n.Catch.Body); err != nil {
			c.popScope()
			return err
		}
		c.popScope()
		if n.Finally != nil {
			c.emit(Op{C: OEnterFinally, L1: endLbl})
			if err := c.genBlock(n.Finally); err != nil {
				return err
			}
			c.emit(Op{C: OEndFinally})
		}
		c.emit(Op{C: OJump, L1: endLbl})
	}

	if n.Finally != nil {
		c.mark(finLbl)
		// Entered during unwind: OEnterFinally records the resume action
		// carried on the unwind frame (return/throw/close continuation).
		c.emit(Op{C: OEnterFinallyUnwind})
		if err := c.genBlock(n.Finally); err != nil {
			return err
		}
		c.emit(Op{C: OEndFinally})
	}
	c.mark(endLbl)
	return nil
}
