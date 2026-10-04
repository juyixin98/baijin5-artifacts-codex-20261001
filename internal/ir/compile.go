package ir

import (
	"fmt"

	"genfsm/internal/ast"
)

// Compile lowers an ast.Program into an ir.Program.
func Compile(prog *ast.Program) (*Program, error) {
	out := &Program{Routines: map[string]*Routine{}}
	for _, f := range prog.Funcs {
		r, err := compileFunc(f)
		if err != nil {
			return nil, err
		}
		out.Routines[f.Name] = r
		out.Order = append(out.Order, f.Name)
	}
	return out, nil
}

// compScope tracks lexical variables of one block. Variables declared in
// nested blocks get distinct slots and never alias enclosing ones; scopes are
// linked for name resolution. This is deliberately simple (no slot reuse) so
// a slot identity stays valid for the whole routine, which is required for
// state-machine frames that persist locals across suspension.
type compScope struct {
	parent *compScope
	vars   map[string]int
}

func (sc *compScope) resolve(name string) (*compScope, int, bool) {
	for cur := sc; cur != nil; cur = cur.parent {
		if s, ok := cur.vars[name]; ok {
			return cur, s, true
		}
	}
	return nil, 0, false
}

type comp struct {
	r        *Routine
	astFn    *ast.Func
	scope    *compScope
	nextSlot int
	nextLbl  Label
	pauseID  int
}

func newComp(f *ast.Func) *comp {
	r := &Routine{Name: f.Name, Params: f.Params, IsGen: f.IsGen, Labels: map[Label]int{}}
	c := &comp{r: r, astFn: f, nextLbl: 1}
	root := &compScope{vars: map[string]int{}}
	for _, p := range f.Params {
		root.vars[p] = c.alloc()
	}
	c.scope = root
	return c
}

func (c *comp) alloc() int {
	s := c.nextSlot
	c.nextSlot++
	if s >= c.r.NSlots {
		c.r.NSlots = s + 1
	}
	return s
}

// declare allocates a fresh slot for name in the current block scope.
func (c *comp) declare(name string) int {
	if _, s, ok := c.scope.resolve(name); ok {
		return s
	}
	s := c.alloc()
	c.scope.vars[name] = s
	return s
}

// local resolves an existing name; undeclared names are declared at root
// (the parser's validator already rejects truly undefined variables).
func (c *comp) local(name string) int {
	if _, s, ok := c.scope.resolve(name); ok {
		return s
	}
	s := c.alloc()
	if c.scope == nil {
		c.scope = &compScope{vars: map[string]int{}}
	}
	c.scope.vars[name] = s
	return s
}

func (c *comp) pushScope() *compScope {
	c.scope = &compScope{parent: c.scope, vars: map[string]int{}}
	return c.scope
}

func (c *comp) popScope() { c.scope = c.scope.parent }

func (c *comp) newLabel() Label {
	l := c.nextLbl
	c.nextLbl++
	return l
}

func (c *comp) mark(l Label) {
	c.r.Labels[l] = len(c.r.Code)
}

func (c *comp) emitAt(pos ast.Pos, op Op) {
	op.Pos = pos.Line*100000 + pos.Col
	c.r.Code = append(c.r.Code, op)
}

func (c *comp) emit(op Op) { c.emitAt(ast.Pos{}, op) }

func (c *comp) intLit(v int64) int64 {
	for i, x := range c.r.NumLits.Ints {
		if x == v {
			return int64(i)
		}
	}
	c.r.NumLits.Ints = append(c.r.NumLits.Ints, v)
	return int64(len(c.r.NumLits.Ints) - 1)
}

func (c *comp) strLit(s string) int64 {
	for i, x := range c.r.NumLits.Strs {
		if x == s {
			return int64(i)
		}
	}
	c.r.NumLits.Strs = append(c.r.NumLits.Strs, s)
	return int64(len(c.r.NumLits.Strs) - 1)
}

func (c *comp) boolLit(b bool) int64 {
	for i, x := range c.r.NumLits.Bools {
		if x == b {
			return int64(i)
		}
	}
	c.r.NumLits.Bools = append(c.r.NumLits.Bools, b)
	return int64(len(c.r.NumLits.Bools) - 1)
}

func compileFunc(f *ast.Func) (*Routine, error) {
	c := newComp(f)
	if err := c.genBlock(f.Body); err != nil {
		return nil, err
	}
	// Fallthrough: implicit return null (bare functions / finished gens).
	c.emit(Op{C: OConstNull})
	c.emit(Op{C: OReturn})
	c.resolve()
	return c.r, nil
}

func (c *comp) resolve() {
	// Rewrite label operands into concrete PCs using a fixed-point-free pass:
	// labels are already recorded at emission of mark().
	rewrite := func(l Label) int {
		if l == 0 {
			return -1
		}
		return c.r.Labels[l]
	}
	for i := range c.r.Code {
		op := &c.r.Code[i]
		switch op.C {
		case OJump, OBranchFalse, OBranchTrue:
			op.J1 = rewrite(op.L1)
		case OSetup:
			op.J1 = rewrite(op.L1)
			op.J2 = rewrite(op.L2)
		case OEnterFinally:
			op.J1 = rewrite(op.L1)
		case OIterNext:
			op.J1 = rewrite(op.L1)
		}
	}
	for i, pp := range c.r.Pauses {
		c.r.Pauses[i].PC = pp.PC
	}
}

// genBlock creates a fresh lexical scope for b.
func (c *comp) genBlock(b *ast.Block) error {
	c.pushScope()
	err := c.genBlockIn(b)
	c.popScope()
	return err
}

// genBlockIn emits statements of b using the current (already-pushed) scope.
func (c *comp) genBlockIn(b *ast.Block) error {
	for _, s := range b.Stmts {
		if err := c.genStmt(s); err != nil {
			return err
		}
	}
	return nil
}

func (c *comp) genStmt(s ast.Stmt) error {
	switch n := s.(type) {
	case *ast.LetStmt:
		if n.Init != nil {
			if err := c.genExpr(n.Init); err != nil {
				return err
			}
		} else {
			c.emit(Op{C: OConstNull})
		}
		c.emit(Op{C: OStore, I: int64(c.declare(n.Name))})
	case *ast.AssignStmt:
		if err := c.genExpr(n.Value); err != nil {
			return err
		}
		name := n.Target.(*ast.NameExpr).Name
		c.emit(Op{C: OStore, I: int64(c.local(name))})
	case *ast.ExprStmt:
		if err := c.genExpr(n.X); err != nil {
			return err
		}
		c.emit(Op{C: OPop})
	case *ast.IfStmt:
		endLbl := c.newLabel()
		if err := c.genCondBranch(n, endLbl); err != nil {
			return err
		}
		c.mark(endLbl)
	case *ast.WhileStmt:
		return c.genWhile(n)
	case *ast.ForStmt:
		return c.genFor(n)
	case *ast.ReturnStmt:
		if n.Value != nil {
			if err := c.genExpr(n.Value); err != nil {
				return err
			}
		} else {
			c.emit(Op{C: OConstNull})
		}
		c.emit(Op{C: OReturn})
	case *ast.ThrowStmt:
		if err := c.genExpr(n.Value); err != nil {
			return err
		}
		c.emitAt(n.P, Op{C: OThrow})
	case *ast.TryStmt:
		if err := c.genTry(n); err != nil {
			return err
		}
	default:
		return fmt.Errorf("ir: unsupported statement %T", s)
	}
	return nil
}

// genCondBranch emits if/else-if/else with forward jumps to end.
func (c *comp) genCondBranch(n *ast.IfStmt, end Label) error {
	if err := c.genExpr(n.Cond); err != nil {
		return err
	}
	elseLbl := c.newLabel()
	c.emitAt(n.P, Op{C: OBranchFalse, L1: elseLbl})
	if err := c.genBlock(n.Then); err != nil {
		return err
	}
	c.emit(Op{C: OJump, L1: end})
	c.mark(elseLbl)
	switch e := n.Else.(type) {
	case nil:
	case *ast.Block:
		if err := c.genBlock(e); err != nil {
			return err
		}
	case *ast.IfStmt:
		if err := c.genCondBranch(e, end); err != nil {
			return err
		}
	default:
		return fmt.Errorf("ir: unsupported else %T", e)
	}
	return nil
}

func (c *comp) genWhile(n *ast.WhileStmt) error {
	start := c.newLabel()
	end := c.newLabel()
	c.mark(start)
	if err := c.genExpr(n.Cond); err != nil {
		return err
	}
	c.emitAt(n.P, Op{C: OBranchFalse, L1: end})
	if err := c.genBlock(n.Body); err != nil {
		return err
	}
	c.emit(Op{C: OJump, L1: start})
	c.mark(end)
	return nil
}
