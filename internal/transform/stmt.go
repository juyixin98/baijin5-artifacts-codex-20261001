package transform

import (
	"fmt"

	"simdc/internal/frontend"
	"simdc/internal/ir"
)

// stmtOrder walks statements in source (DFS pre) order and returns the
// global statement number used for fault attribution.
func stmtOrder(stmts []frontend.Stmt) []frontend.Stmt {
	var out []frontend.Stmt
	var walk func([]frontend.Stmt)
	walk = func(ss []frontend.Stmt) {
		for _, s := range ss {
			out = append(out, s)
			if f, ok := s.(*frontend.IfStmt); ok {
				walk(f.Then)
				walk(f.Else)
			}
		}
	}
	walk(stmts)
	return out
}

func stmtIDOf(ordered []frontend.Stmt, target frontend.Stmt) int {
	for i, s := range ordered {
		if s == target {
			return i
		}
	}
	return -1
}

func (l *lower) lowerStmts(stmts []frontend.Stmt, guard ir.Mask, ordered []frontend.Stmt) error {
	for _, s := range stmts {
		switch t := s.(type) {
		case *frontend.AssignStmt:
			id := stmtIDOf(ordered, t)
			l.curStmt = id
			if err := l.lowerAssign(t, guard, id); err != nil {
				return err
			}
		case *frontend.IfStmt:
			l.curStmt = stmtIDOf(ordered, t)
			cv, err := l.lowerExpr(t.Cond, guard)
			if err != nil {
				return err
			}
			cm := l.compareMask(ir.Neq, cv, l.constReg(0), guard)
			thenMask := l.andMask(guard, cm)
			if err := l.lowerStmts(t.Then, thenMask, ordered); err != nil {
				return err
			}
			if len(t.Else) > 0 {
				elseMask := l.andNotMask(guard, cm)
				if err := l.lowerStmts(t.Else, elseMask, ordered); err != nil {
					return err
				}
			}
		default:
			return fmt.Errorf("unsupported statement %T", s)
		}
	}
	return nil
}

func (l *lower) lowerAssign(t *frontend.AssignStmt, guard ir.Mask, stmtID int) error {
	val, err := l.lowerExpr(t.Rhs, guard)
	if err != nil {
		return err
	}
	d := l.info.Outputs[t.Name]
	if d.Kind == frontend.KindScalar {
		// Scalar output: guarded blend semantics are not modeled as memory;
		// the language restricts interesting outputs to arrays for masked
		// lanes. Scalar outputs are only produced by reductions.
		return fmt.Errorf("scalar output %q may only be assigned by reduce", t.Name)
	}
	idx, err := l.lowerExpr(t.Idx, guard)
	if err != nil {
		return err
	}
	l.body = append(l.body, ir.Instr{
		Op: ir.OpStore, Name: t.Name, A: idx, B: val, Mask: guard, StmtID: stmtID,
	})
	return nil
}
