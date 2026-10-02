package transform

import (
	"fmt"

	"simdc/internal/frontend"
	"simdc/internal/ir"
)

type lower struct {
	width int
	info  *frontend.Info
	prog  *frontend.Program

	body []ir.Instr

	regs  int
	masks int

	// constants interned by value
	consts map[int64]ir.Reg
	// scalar-array? no; scalar input registers interned by name
	scalars  map[string]ir.Reg
	indexVar string
	curStmt  int
}

func newLower(width int, info *frontend.Info, prog *frontend.Program) *lower {
	return &lower{
		width:   width,
		info:    info,
		prog:    prog,
		masks:   1, // mask id 0 is reserved as "no predicate" sentinel
		consts:  map[int64]ir.Reg{},
		scalars: map[string]ir.Reg{},
		curStmt: -1,
	}
}

func (l *lower) newReg() ir.Reg {
	r := ir.Reg(l.regs)
	l.regs++
	return r
}

func (l *lower) newMask() ir.Mask {
	m := ir.Mask(l.masks)
	l.masks++
	return m
}

func (l *lower) constReg(v int64) ir.Reg {
	if r, ok := l.consts[v]; ok {
		return r
	}
	r := l.newReg()
	l.body = append(l.body, ir.Instr{Op: ir.OpConst, Dst: r, Imm: v, StmtID: -1})
	l.consts[v] = r
	return r
}

func (l *lower) scalarReg(name string) (ir.Reg, error) {
	if r, ok := l.scalars[name]; ok {
		return r, nil
	}
	d, ok := l.info.Inputs[name]
	if !ok || d.Kind != frontend.KindScalar {
		od, ok2 := l.info.Outputs[name]
		if !ok2 || od.Kind != frontend.KindScalar {
			return 0, fmt.Errorf("unknown scalar %q", name)
		}
	}
	r := l.newReg()
	l.body = append(l.body, ir.Instr{Op: ir.OpLoadS, Dst: r, Name: name, StmtID: -1})
	l.scalars[name] = r
	return r, nil
}

// lowerExpr lowers a value expression. safe is the mask of lanes for which
// the surrounding statement is definitely executed; it is threaded into
// trapping ops (/, %) and gathers so masked lanes never fault. Returns the
// value register and the mask under which the value is valid (== safety mask
// for trapping producers, and the broader safe mask otherwise).
func (l *lower) lowerExpr(e frontend.Expr, safe ir.Mask) (ir.Reg, error) {
	switch t := e.(type) {
	case *frontend.IntLit:
		return l.constReg(t.Value), nil
	case *frontend.Ident:
		if t.Name == l.indexVar {
			r := l.newReg()
			l.body = append(l.body, ir.Instr{Op: ir.OpIVar, Dst: r, StmtID: -1})
			return r, nil
		}
		return l.scalarReg(t.Name)
	case *frontend.IndexExpr:
		idx, err := l.lowerExpr(t.Idx, safe)
		if err != nil {
			return 0, err
		}
		r := l.newReg()
		l.body = append(l.body, ir.Instr{
			Op: ir.OpLoad, Dst: r, A: idx, Mask: safe, Name: t.Name, StmtID: l.curStmt,
		})
		return r, nil
	case *frontend.LenExpr:
		n := l.info.Lengths[t.Name]
		return l.constReg(n), nil
	case *frontend.UnaryExpr:
		inner, err := l.lowerExpr(t.Inner, safe)
		if err != nil {
			return 0, err
		}
		if t.Op == "!" {
			return 0, fmt.Errorf("logical not must produce a mask")
		}
		zero := l.constReg(0)
		r := l.newReg()
		bop := map[string]ir.BinOp{"-": ir.Sub, "~": ir.Xor}[t.Op]
		l.body = append(l.body, ir.Instr{
			Op: ir.OpBin, Dst: r, A: zero, B: inner, Bop: bop, Mask: safe, StmtID: -1,
		})
		return r, nil
	case *frontend.BinaryExpr:
		return l.lowerBinary(t, safe)
	}
	return 0, fmt.Errorf("cannot lower expression %T", e)
}

func (l *lower) lowerBinary(t *frontend.BinaryExpr, safe ir.Mask) (ir.Reg, error) {
	switch t.Op {
	case "&&":
		// Short-circuit semantics: left evaluated under safe; right only under
		// (safe AND left). Produce a value register (1/0) via compares of the
		// boolean operands against zero, combining masks.
		lv, err := l.lowerExpr(t.Left, safe)
		if err != nil {
			return 0, err
		}
		lm := l.compareMask(ir.Neq, lv, l.constReg(0), safe)
		both := l.andMask(safe, lm)
		rv, err := l.lowerExpr(t.Right, both)
		if err != nil {
			return 0, err
		}
		rm := l.compareMask(ir.Neq, rv, l.constReg(0), both)
		resMask := l.andMask(both, rm)
		return l.oneZeroReg(safe, resMask, safe), nil
	case "||":
		lv, err := l.lowerExpr(t.Left, safe)
		if err != nil {
			return 0, err
		}
		lm := l.compareMask(ir.Neq, lv, l.constReg(0), safe)
		rest := l.andNotMask(safe, lm)
		rv, err := l.lowerExpr(t.Right, rest)
		if err != nil {
			return 0, err
		}
		rm := l.compareMask(ir.Neq, rv, l.constReg(0), rest)
		resMask := l.orMasks(safe, lm, rm)
		return l.oneZeroReg(safe, resMask, safe), nil
	}
	left, err := l.lowerExpr(t.Left, safe)
	if err != nil {
		return 0, err
	}
	right, err := l.lowerExpr(t.Right, safe)
	if err != nil {
		return 0, err
	}
	bop, ok := arithOps[t.Op]
	if ok {
		r := l.newReg()
		in := ir.Instr{Op: ir.OpBin, Dst: r, A: left, B: right, Bop: bop, Mask: safe, StmtID: -1}
		in.StmtID = l.curStmt
		// Non-trapping arithmetic still tags safe for uniform semantics.
		l.body = append(l.body, in)
		return r, nil
	}
	cop, ok := cmpOps[t.Op]
	if ok {
		m := l.compareMask(cop, left, right, safe)
		return l.oneZeroReg(safe, m, safe), nil
	}
	return 0, fmt.Errorf("unsupported binary op %q", t.Op)
}

func (l *lower) compareMask(cop ir.CmpOp, a, b ir.Reg, under ir.Mask) ir.Mask {
	m := l.newMask()
	l.body = append(l.body, ir.Instr{
		Op: ir.OpCmp, MDst: m, A: a, B: b, Cop: cop, Mask: under, StmtID: -1,
	})
	return m
}

func (l *lower) andMask(a, b ir.Mask) ir.Mask {
	m := l.newMask()
	l.body = append(l.body, ir.Instr{Op: ir.OpAnd, MDst: m, Mask: a, Imm: int64(b), StmtID: -1})
	return m
}

// andNotMask returns a AND (NOT b).
func (l *lower) andNotMask(a, b ir.Mask) ir.Mask {
	nb := l.newMask()
	l.body = append(l.body, ir.Instr{Op: ir.OpNot, MDst: nb, Mask: b, StmtID: -1})
	return l.andMask(a, nb)
}

func (l *lower) orMasks(under, a, b ir.Mask) ir.Mask {
	// a OR b under a single safety region: NOT(NOT a AND NOT b).
	na := l.newMask()
	l.body = append(l.body, ir.Instr{Op: ir.OpNot, MDst: na, Mask: a, StmtID: -1})
	nb := l.newMask()
	l.body = append(l.body, ir.Instr{Op: ir.OpNot, MDst: nb, Mask: b, StmtID: -1})
	neither := l.andMask(na, nb)
	res := l.newMask()
	l.body = append(l.body, ir.Instr{Op: ir.OpNot, MDst: res, Mask: neither, StmtID: -1})
	return res
}

// oneZeroReg materializes boolean mask truth as 1/0 in a value register.
// valueTrue is the mask where the value is 1; lanes outside valueTrue but in
// active get 0. The runtime blanks masked lanes anyway.
func (l *lower) oneZeroReg(active, valueTrue, _ ir.Mask) ir.Reg {
	one := l.constReg(1)
	zero := l.constReg(0)
	r := l.newReg()
	// Emit a compare-driven select using VBIN: r = valueTrue ? 1 : 0.
	l.body = append(l.body, ir.Instr{
		Op: ir.OpBin, Dst: r, A: one, B: zero, Bop: ir.Sel, Mask: valueTrue, StmtID: -1,
	})
	_ = active
	return r
}

var arithOps = map[string]ir.BinOp{
	"+": ir.Add, "-": ir.Sub, "*": ir.Mul, "/": ir.Quo, "%": ir.Rem,
	"&": ir.And, "|": ir.Or, "^": ir.Xor,
}

var cmpOps = map[string]ir.CmpOp{
	"==": ir.Eq, "!=": ir.Neq, "<": ir.Lt, ">": ir.Gt, "<=": ir.Le, ">=": ir.Ge,
}
