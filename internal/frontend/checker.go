package frontend

import "fmt"

// Info is the checked program information consumed by lowering/execution.
type Info struct {
	Inputs  map[string]*Decl
	Outputs map[string]*Decl
	// Lengths holds compile-time constant array lengths keyed by name.
	Lengths map[string]int64
}

// Check performs semantic checks: unique declarations, assign-only outputs,
// array/scalar arity, counted-loop form, and reduce form. All frontend
// failures are rejection-class errors.
func Check(prog *Program) (*Info, error) {
	info := &Info{
		Inputs:  map[string]*Decl{},
		Outputs: map[string]*Decl{},
		Lengths: map[string]int64{},
	}
	declare := func(d *Decl, out bool) error {
		if _, ok := info.Inputs[d.Name]; ok {
			return fmt.Errorf("%s: %q already declared", d.Pos(), d.Name)
		}
		if _, ok := info.Outputs[d.Name]; ok {
			return fmt.Errorf("%s: %q already declared", d.Pos(), d.Name)
		}
		if d.Kind == KindArray {
			n, err := evalConst(d.Length)
			if err != nil {
				return fmt.Errorf("%s: array length of %q: %w", d.Pos(), d.Name, err)
			}
			if n <= 0 {
				return fmt.Errorf("%s: array %q length must be positive, got %d", d.Pos(), d.Name, n)
			}
			info.Lengths[d.Name] = n
		}
		if out {
			info.Outputs[d.Name] = d
		} else {
			info.Inputs[d.Name] = d
		}
		return nil
	}
	for _, d := range prog.Inputs {
		if err := declare(d, false); err != nil {
			return nil, err
		}
	}
	for _, d := range prog.Outputs {
		if err := declare(d, true); err != nil {
			return nil, err
		}
	}
	if len(prog.Body) == 0 {
		return nil, fmt.Errorf("program must contain at least one for loop")
	}
	seenLoops := map[string]bool{}
	for _, st := range prog.Body {
		f, ok := st.(*ForStmt)
		if !ok {
			return nil, fmt.Errorf("%s: only counted for loops allowed at top level", st.stmtPos())
		}
		if err := checkFor(f, info, seenLoops, true); err != nil {
			return nil, err
		}
	}
	for _, r := range prog.Reduces {
		if err := checkReduce(r, info, seenLoops); err != nil {
			return nil, err
		}
	}
	return info, nil
}

func checkFor(f *ForStmt, info *Info, seen map[string]bool, top bool) error {
	if !top {
		return fmt.Errorf("%s: nested loops are not supported", f.Pos)
	}
	if _, err := constValue(f.Begin); err != nil {
		// Loop begin may be 0 constant only (checked by evalConst in bounds later).
	}
	if f.Var == "" {
		return fmt.Errorf("%s: missing loop variable", f.Pos)
	}
	if seen[f.Var] {
		return fmt.Errorf("%s: loop variable %q already used", f.Pos, f.Var)
	}
	seen[f.Var] = true
	env := map[string]bool{f.Var: true}
	for _, s := range f.Body {
		if err := checkStmt(s, info, env); err != nil {
			return err
		}
	}
	return nil
}

func checkStmt(s Stmt, info *Info, env map[string]bool) error {
	switch t := s.(type) {
	case *AssignStmt:
		d, ok := info.Outputs[t.Name]
		if !ok {
			return fmt.Errorf("%s: assignment target %q must be a declared output", t.Pos, t.Name)
		}
		if t.Idx == nil {
			if d.Kind != KindScalar {
				return fmt.Errorf("%s: array output %q requires index", t.Pos, t.Name)
			}
		} else {
			if d.Kind != KindArray {
				return fmt.Errorf("%s: scalar output %q cannot be indexed", t.Pos, t.Name)
			}
			if err := checkExpr(t.Idx, info, env); err != nil {
				return err
			}
		}
		return checkExpr(t.Rhs, info, env)
	case *IfStmt:
		if err := checkExpr(t.Cond, info, env); err != nil {
			return err
		}
		for _, c := range t.Then {
			if err := checkStmt(c, info, env); err != nil {
				return err
			}
		}
		for _, c := range t.Else {
			if err := checkStmt(c, info, env); err != nil {
				return err
			}
		}
		return nil
	case *ForStmt:
		return fmt.Errorf("%s: nested loops are not supported", t.Pos)
	default:
		return fmt.Errorf("%s: unsupported statement", s.stmtPos())
	}
}

func checkReduce(r *ReduceStmt, info *Info, seen map[string]bool) error {
	d, ok := info.Inputs[r.Source]
	if !ok || d.Kind != KindArray {
		return fmt.Errorf("%s: reduce source %q must be an input array", r.Pos, r.Source)
	}
	tgt, ok := info.Outputs[r.Target]
	if !ok || tgt.Kind != KindScalar {
		return fmt.Errorf("%s: reduce target %q must be a scalar output", r.Pos, r.Target)
	}
	if r.Op != "+" && r.Op != "*" && r.Op != "concat" {
		return fmt.Errorf("%s: unsupported reduce op %q", r.Pos, r.Op)
	}
	if r.LoopVar == "" || seen[r.LoopVar] {
		// Dedicated reduce loop may reuse a fresh variable; require uniqueness.
		if seen[r.LoopVar] {
			return fmt.Errorf("%s: reduce loop variable %q already used", r.Pos, r.LoopVar)
		}
	}
	seen[r.LoopVar] = true
	env := map[string]bool{r.LoopVar: true}
	idx, ok := r.SrcIdx.(*Ident)
	if !ok || idx.Name != r.LoopVar {
		return fmt.Errorf("%s: reduce source must be indexed by its loop variable %s[i]", r.Pos, r.Source)
	}
	if err := checkExpr(r.SrcIdx, info, env); err != nil {
		return err
	}
	begin, err := evalConst(r.Begin)
	if err != nil || begin != 0 {
		return fmt.Errorf("%s: reduce loop must start at constant 0", r.Pos)
	}
	le, ok := r.End.(*LenExpr)
	if !ok || le.Name != r.Source {
		return fmt.Errorf("%s: reduce loop upper bound must be len(%s)", r.Pos, r.Source)
	}
	return nil
}

func checkExpr(e Expr, info *Info, env map[string]bool) error {
	switch t := e.(type) {
	case *IntLit:
		return nil
	case *Ident:
		if env[t.Name] {
			return nil
		}
		if d, ok := info.Inputs[t.Name]; ok && d.Kind == KindScalar {
			return nil
		}
		if d, ok := info.Outputs[t.Name]; ok && d.Kind == KindScalar {
			return nil
		}
		return fmt.Errorf("%s: undefined scalar %q", t.Pos, t.Name)
	case *IndexExpr:
		if d, ok := info.Inputs[t.Name]; ok && d.Kind == KindArray {
			return checkExpr(t.Idx, info, env)
		}
		return fmt.Errorf("%s: %q is not a declared input array", t.Pos, t.Name)
	case *LenExpr:
		if d, ok := info.Inputs[t.Name]; ok && d.Kind == KindArray {
			_ = d
			return nil
		}
		if d, ok := info.Outputs[t.Name]; ok && d.Kind == KindArray {
			_ = d
			return nil
		}
		return fmt.Errorf("%s: len() requires a declared array, got %q", t.Pos, t.Name)
	case *UnaryExpr:
		return checkExpr(t.Inner, info, env)
	case *BinaryExpr:
		if err := checkExpr(t.Left, info, env); err != nil {
			return err
		}
		return checkExpr(t.Right, info, env)
	}
	return fmt.Errorf("unsupported expression")
}

func (d *Decl) Pos() Pos {
	// Declarations do not retain a dedicated position; length carries it.
	if d.Length != nil {
		return d.Length.exprPos()
	}
	return Pos{Line: 1, Col: 1}
}

func evalConst(e Expr) (int64, error) {
	switch t := e.(type) {
	case *IntLit:
		return t.Value, nil
	case *UnaryExpr:
		if t.Op == "-" {
			v, err := evalConst(t.Inner)
			if err != nil {
				return 0, err
			}
			return -v, nil
		}
	case *BinaryExpr:
		l, err := evalConst(t.Left)
		if err != nil {
			return 0, err
		}
		r, err := evalConst(t.Right)
		if err != nil {
			return 0, err
		}
		return applyConst(t.Op, l, r)
	}
	return 0, fmt.Errorf("not a constant expression")
}

func applyConst(op string, l, r int64) (int64, error) {
	switch op {
	case "+":
		return l + r, nil
	case "-":
		return l - r, nil
	case "*":
		return l * r, nil
	}
	return 0, fmt.Errorf("operator %q not allowed in constant expression", op)
}

// constValue is a soft constant evaluation used for loop bounds during checks;
// actual bound resolution happens at request time and may be UNDETERMINED.
func constValue(e Expr) (int64, error) { return evalConst(e) }

// EvalConstExported exposes compile-time constant evaluation for pipeline
// input-length validation.
func EvalConstExported(e Expr) (int64, error) { return evalConst(e) }
