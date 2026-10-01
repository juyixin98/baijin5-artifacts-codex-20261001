// Package lower compiles an ast.Root into an ir.Program.
//
// Layout for each lexical scope
// -----------------------------
//
//	<... body statements and inline destructor bodies ...>
//	jmp L_cleanup
//	L_cleanup:
//	  [per acquired resource, in reverse acquisition order:]
//	    guard_check slot -> L_next
//	    guard_clear slot
//	    jmp L_destructor
//	    L_next:
//	  route normal=N break=B return=R fail=F
//
// Each acquire is compiled as:
//
//	acquire slot,point -> L_cleanup          // host failure skips guard set
//	jmp L_after_dx                           // success: destructor deferred
//	L_destructor: <emit/cleanup_fail> ; jmp L_cleanup
//	L_after_dx:
//
// Every unwind reason converges on the scope's single cleanup section; the
// guard check+clear makes that shared path idempotent, which is the lowering
// guarantee behind "only successfully initialized objects, in reverse, never
// twice on break/return/fail shared paths".
//
// Repeat loops own the body scope; the back edge resets the exact guard
// slots owned by the iteration and decrements a remaining-iteration counter.
package lower

import (
	"fmt"
	"strconv"

	"scopelang/internal/ast"
	"scopelang/internal/diag"
	"scopelang/internal/ir"
)

type slotRec struct {
	slot      int
	ordinal   int
	name      string
	expr      string
	initPoint string
	bodyStart string
	line      int
	inRepeat  bool
}

type scope struct {
	loop    bool
	slots   []*slotRec
	normal  string // continuation for reason=normal
	brk     string // cleanup label of the enclosing loop (loop absorbs break)
	ret     string // cleanup label of the root scope
	fail    string // cleanup label of the root scope
	cleanup string
}

type marker struct {
	label string
	off   int
}

type Compiler struct {
	ins    []ir.Instruction
	marks  []marker
	scopes []*scope
	slotN  int
	tempN  int
	labelN int
	points []ir.PointInfo
}

func Compile(root *ast.Root) (*ir.Program, error) {
	if root == nil || root.Body == nil {
		return nil, diag.New(diag.Internal, "LOWER_EMPTY", "nil root").With(diag.PhaseLower, "", 0)
	}
	c := &Compiler{}
	rootSc := c.pushRootScope()
	c.compileStmts(root.Body.Stmts)
	c.jmp(rootSc.cleanup)
	c.emitCleanup(rootSc, "__halt_ok")
	c.emit(ir.Instruction{Op: ir.OpHaltOK})
	c.emit(ir.Instruction{Op: ir.OpHaltReturn})
	c.emit(ir.Instruction{Op: ir.OpHaltFail})
	if err := c.resolveLabels(); err != nil {
		return nil, err
	}
	return &ir.Program{
		Instructions: c.ins,
		Labels:       c.labelMap(),
		Points:       c.points,
		Slots:        c.slotN,
		Temps:        c.tempN,
	}, nil
}

func (c *Compiler) newLabel(kind string) string {
	c.labelN++
	return fmt.Sprintf("L%d_%s", c.labelN, kind)
}

func (c *Compiler) pushRootScope() *scope {
	sc := &scope{
		loop: false, normal: "__halt_ok", brk: "",
		ret: "__halt_return", fail: "__halt_fail",
		cleanup: c.newLabel("cleanup"),
	}
	c.scopes = append(c.scopes, sc)
	return sc
}

// pushChildScope computes continuation targets from the parent. A loop scope
// absorbs break (it has no break target); non-loop scopes forward break to
// the nearest enclosing loop's cleanup. return/fail always propagate toward
// the root cleanup, so every scope points there directly.
func (c *Compiler) pushChildScope(parent *scope, loop bool, normal string) *scope {
	brk := ""
	if !loop {
		for i := len(c.scopes) - 1; i >= 0; i-- {
			if c.scopes[i].loop {
				brk = c.scopes[i].cleanup
				break
			}
		}
	}
	rootCleanup := c.scopes[0].cleanup
	sc := &scope{
		loop: loop, normal: normal, brk: brk,
		ret: rootCleanup, fail: rootCleanup,
		cleanup: c.newLabel("cleanup"),
	}
	c.scopes = append(c.scopes, sc)
	return sc
}

func (c *Compiler) emit(ins ir.Instruction) { c.ins = append(c.ins, ins) }
func (c *Compiler) jmp(target string)       { c.emit(ir.Instruction{Op: ir.OpJmp, Target: target}) }
func (c *Compiler) mark(label string)       { c.marks = append(c.marks, marker{label: label, off: len(c.ins)}) }
func (c *Compiler) cur() *scope             { return c.scopes[len(c.scopes)-1] }

func (c *Compiler) compileStmts(stmts []ast.Stmt) {
	for _, s := range stmts {
		c.compileStmt(s)
	}
}

func (c *Compiler) loopCleanup() string {
	for i := len(c.scopes) - 1; i >= 0; i-- {
		if c.scopes[i].loop {
			return c.scopes[i].cleanup
		}
	}
	return ""
}

func (c *Compiler) compileStmt(s ast.Stmt) {
	switch n := s.(type) {
	case *ast.Acquire:
		c.compileAcquire(n)
	case *ast.Block:
		c.compileBlock(n)
	case *ast.Repeat:
		c.compileRepeat(n)
	case *ast.Emit:
		c.emit(ir.Instruction{Op: ir.OpEmit, Text: n.Text, Line: n.Line})
	case *ast.Fail:
		c.emit(ir.Instruction{Op: ir.OpUnwind, Reason: ir.RFail, Point: n.Point,
			Target: c.cur().cleanup, Line: n.Line})
	case *ast.Break:
		c.emit(ir.Instruction{Op: ir.OpUnwind, Reason: ir.RBreak,
			Target: c.loopCleanup(), Line: n.Line})
	case *ast.Return:
		c.emit(ir.Instruction{Op: ir.OpUnwind, Reason: ir.RReturn, Value: n.Value,
			Target: c.cur().cleanup, Line: n.Line})
	}
}

func (c *Compiler) compileAcquire(n *ast.Acquire) {
	sc := c.cur()
	slot := c.slotN
	c.slotN++
	rec := &slotRec{
		slot: slot, ordinal: ordinalOf(n.Point), name: n.Name, expr: n.ResExpr,
		initPoint: n.Point, line: n.Line, inRepeat: sc.loop,
	}
	dx := c.newLabel("dx")
	afterDx := c.newLabel("afterdx")
	rec.bodyStart = dx
	// Host failure jumps to this scope's cleanup while the guard is still
	// clear, so the failed resource is never destroyed.
	c.emit(ir.Instruction{
		Op: ir.OpAcquire, Slot: slot, Point: n.Point, Name: n.Name,
		Expr: n.ResExpr, Target: sc.cleanup, Line: n.Line,
	})
	c.jmp(afterDx)
	c.mark(dx)
	c.points = append(c.points, ir.PointInfo{
		Point: n.Point, Kind: "init", Ordinal: rec.ordinal, Name: n.Name,
		Expr: n.ResExpr, Line: n.Line, InRepeat: sc.loop,
	})
	for _, cs := range n.Cleanup.Stmts {
		switch cn := cs.(type) {
		case *ast.Emit:
			c.emit(ir.Instruction{Op: ir.OpEmit, Text: cn.Text, Line: cn.Line})
		case *ast.Fail:
			c.emit(ir.Instruction{Op: ir.OpCleanupFail, Point: cn.Point, Line: cn.Line})
			c.points = append(c.points, ir.PointInfo{
				Point: cn.Point, Kind: "close", Ordinal: rec.ordinal,
				Name: n.Name, Line: cn.Line, InRepeat: sc.loop,
			})
		}
	}
	c.jmp(sc.cleanup)
	c.mark(afterDx)
	sc.slots = append(sc.slots, rec)
}

func (c *Compiler) compileBlock(b *ast.Block) {
	parent := c.cur()
	cont := c.newLabel("after")
	sc := c.pushChildScope(parent, false, cont)
	c.compileStmts(b.Stmts)
	c.jmp(sc.cleanup)
	c.emitCleanup(sc, cont)
	c.mark(cont)
}

func (c *Compiler) compileRepeat(r *ast.Repeat) {
	check := c.newLabel("check")
	bodyStart := c.newLabel("body")
	afterPop := c.newLabel("afterpop")
	after := c.newLabel("after")
	temp := c.tempN
	c.tempN++
	c.emit(ir.Instruction{Op: ir.OpSetCounter, Temp: temp, Count: r.Count, Line: r.Line})
	c.jmp(check)
	c.mark(bodyStart)
	c.emit(ir.Instruction{Op: ir.OpIterEnter, Temp: temp, Count: r.Count})
	parent := c.cur()
	sc := c.pushChildScope(parent, true, check)
	sc.brk = afterPop // break leaves via pop, then continuation
	c.compileStmts(r.Body.Stmts)
	c.jmp(sc.cleanup)
	c.emitCleanup(sc, check)
	// Back edge.
	c.mark(check)
	c.emit(ir.Instruction{Op: ir.OpCounterCheck, Temp: temp, Target: after})
	c.emit(ir.Instruction{Op: ir.OpCounterDecr, Temp: temp})
	slots := make([]int, 0, len(sc.slots))
	for _, rec := range sc.slots {
		slots = append(slots, rec.slot)
	}
	if len(slots) > 0 {
		c.emit(ir.Instruction{Op: ir.OpResetGuards, Slots: slots})
	}
	c.jmp(bodyStart)
	c.mark(afterPop)
	c.emit(ir.Instruction{Op: ir.OpIterLeave})
	c.mark(after)
}

// emitCleanup writes the shared cleanup section and pops the current scope.
func (c *Compiler) emitCleanup(sc *scope, normal string) {
	c.mark(sc.cleanup)
	for i := len(sc.slots) - 1; i >= 0; i-- {
		rec := sc.slots[i]
		next := c.newLabel("next")
		c.emit(ir.Instruction{Op: ir.OpGuardCheck, Slot: rec.slot, Target: next})
		c.emit(ir.Instruction{Op: ir.OpGuardClear, Slot: rec.slot})
		c.jmp(rec.bodyStart)
		c.mark(next)
	}
	if sc.loop {
		// The loop absorbs break: after iteration cleanups a break reason
		// becomes normal and control leaves the loop.
		c.emit(ir.Instruction{Op: ir.OpBreakAbsorb,
			Normal: normal, Break: sc.brk, Return: sc.ret, Fail: sc.fail})
	} else {
		c.emit(ir.Instruction{Op: ir.OpRoute,
			Normal: normal, Break: sc.brk, Return: sc.ret, Fail: sc.fail})
	}
	c.scopes = c.scopes[:len(c.scopes)-1]
}

func (c *Compiler) resolveLabels() error {
	addr := map[string]int{}
	for _, m := range c.marks {
		addr[m.label] = m.off
	}
	nIns := len(c.ins)
	addr["__halt_ok"] = nIns - 3
	addr["__halt_return"] = nIns - 2
	addr["__halt_fail"] = nIns - 1
	num := func(label string) (string, error) {
		if label == "" {
			return "", nil
		}
		a, ok := addr[label]
		if !ok {
			return "", diag.New(diag.Internal, "UNRESOLVED_LABEL", "label "+label).
				With(diag.PhaseLower, "", 0)
		}
		return strconv.Itoa(a), nil
	}
	for i := range c.ins {
		in := &c.ins[i]
		var err error
		switch in.Op {
		case ir.OpJmp, ir.OpGuardCheck, ir.OpCounterCheck, ir.OpAcquire:
			in.Target, err = num(in.Target)
		case ir.OpRoute, ir.OpBreakAbsorb:
			if in.Normal, err = num(in.Normal); err != nil {
				return err
			}
			if in.Break, err = num(in.Break); err != nil {
				return err
			}
			if in.Return, err = num(in.Return); err != nil {
				return err
			}
			if in.Fail, err = num(in.Fail); err != nil {
				return err
			}
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func (c *Compiler) labelMap() map[string]int {
	m := map[string]int{}
	for _, mk := range c.marks {
		m[mk.label] = mk.off
	}
	nIns := len(c.ins)
	m["__halt_ok"] = nIns - 3
	m["__halt_return"] = nIns - 2
	m["__halt_fail"] = nIns - 1
	return m
}

func ordinalOf(point string) int {
	n := 0
	for i := 0; i < len(point); i++ {
		if point[i] >= '0' && point[i] <= '9' {
			n = n*10 + int(point[i]-'0')
		}
	}
	return n
}
