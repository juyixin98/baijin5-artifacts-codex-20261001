// Package reference is an independent scalar evaluator for the source
// language. It walks the frontend AST directly and shares no execution code
// with the SIMD interpreter: the only shared package is internal/sem (plain
// result/fault structs). It is therefore suitable as the differential oracle
// for the lowered masked IR.
package reference

import (
	"fmt"

	"simdc/internal/frontend"
	"simdc/internal/sem"
)

// Data holds concrete request inputs and output declarations.
type Data struct {
	Scalars        map[string]int64
	Arrays         map[string][]int64
	OutputArrayLen map[string]int
	OutputScalars  []string
	TripCount      int64
}

type refFault struct {
	gi     int
	stmtID int
	stage  sem.Stage
	code   sem.FaultCode
	detail string
}

type env struct {
	scalars map[string]int64
	arrays  map[string][]int64
	outputs map[string][]int64
	loopVar string
	index   int64
}

func (e *env) getScalar(name string) (int64, bool) {
	if name == e.loopVar {
		return e.index, true
	}
	if v, ok := e.scalars[name]; ok {
		return v, true
	}
	return 0, false
}

// Run executes prog as ordinary scalar code.
func Run(prog *frontend.Program, info *frontend.Info, d Data) (*sem.Result, error) {
	outs := map[string][]int64{}
	for name, n := range d.OutputArrayLen {
		outs[name] = make([]int64, n)
	}
	outScalars := map[string]int64{}
	for _, name := range d.OutputScalars {
		outScalars[name] = 0
	}
	if len(prog.Body) != 1 {
		return nil, fmt.Errorf("reference expects exactly one loop, got %d", len(prog.Body))
	}
	loop := prog.Body[0].(*frontend.ForStmt)
	ordered := orderStmts(loop.Body)
	idOf := func(s frontend.Stmt) int {
		for i, x := range ordered {
			if x == s {
				return i
			}
		}
		return -1
	}

	e := &env{
		scalars: d.Scalars, arrays: d.Arrays, outputs: outs,
		loopVar: loop.Var,
	}
	var halted *refFault
bodyLoop:
	for i := int64(0); i < d.TripCount; i++ {
		e.index = i
		for _, s := range loop.Body {
			f, err := e.execStmt(s, idOf)
			if err != nil {
				return nil, err
			}
			if f != nil {
				f.gi = int(i)
				f.stage = sem.StageBody
				halted = f
				break bodyLoop
			}
		}
	}

	if halted == nil {
	reduceLoop:
		for _, r := range prog.Reduces {
			arr := d.Arrays[r.Source]
			acc := identity(r.Op)
			for i := int64(0); i < d.TripCount; i++ {
				if i < 0 || i >= int64(len(arr)) {
					halted = &refFault{
						gi: int(i), stmtID: -1, stage: sem.StageReduce,
						code:   sem.FaultReduceOOB,
						detail: fmt.Sprintf("reduce read %s[%d] len=%d", r.Source, i, len(arr)),
					}
					break reduceLoop
				}
				acc = applyRed(r.Op, acc, arr[i])
			}
			if halted == nil {
				outScalars[r.Target] = acc
			}
		}
	}
	res := &sem.Result{
		Halted:  halted != nil,
		Outputs: sem.Outputs{Scalars: outScalars, Arrays: outs},
	}
	if halted != nil {
		res.Fault = &sem.Fault{
			Code: halted.code, GlobalIdx: halted.gi, StmtID: halted.stmtID,
			Stage: halted.stage, Detail: halted.detail,
		}
	}
	return res, nil
}

func orderStmts(stmts []frontend.Stmt) []frontend.Stmt {
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

func (e *env) execStmt(s frontend.Stmt, idOf func(frontend.Stmt) int) (*refFault, error) {
	switch t := s.(type) {
	case *frontend.AssignStmt:
		v, f, err := e.eval(t.Rhs)
		if err != nil {
			return nil, err
		}
		if f != nil {
			f.stmtID = idOf(t)
			return f, nil
		}
		if t.Idx == nil {
			return nil, fmt.Errorf("scalar output assignment not supported in body")
		}
		idx, f2, err := e.eval(t.Idx)
		if err != nil {
			return nil, err
		}
		if f2 != nil {
			f2.stmtID = idOf(t)
			return f2, nil
		}
		dst := e.outputs[t.Name]
		if idx < 0 || idx >= int64(len(dst)) {
			return &refFault{
				code:   sem.FaultOutputOOB,
				detail: fmt.Sprintf("write %s[%d] len=%d", t.Name, idx, len(dst)),
				stmtID: idOf(t),
			}, nil
		}
		dst[idx] = v
		return nil, nil
	case *frontend.IfStmt:
		cv, f, err := e.eval(t.Cond)
		if err != nil {
			return nil, err
		}
		if f != nil {
			f.stmtID = idOf(t)
			return f, nil
		}
		branch := t.Else
		if cv != 0 {
			branch = t.Then
		}
		if cv == 0 && len(t.Else) == 0 {
			return nil, nil
		}
		for _, c := range branch {
			cf, err := e.execStmt(c, idOf)
			if err != nil {
				return nil, err
			}
			if cf != nil {
				return cf, nil
			}
		}
		return nil, nil
	}
	return nil, fmt.Errorf("unsupported statement %T", s)
}

// eval returns (value, fault, error). A fault is an in-language trap; error
// is a harness/config problem.
func (e *env) eval(x frontend.Expr) (int64, *refFault, error) {
	switch t := x.(type) {
	case *frontend.IntLit:
		return t.Value, nil, nil
	case *frontend.Ident:
		v, ok := e.getScalar(t.Name)
		if !ok {
			return 0, nil, fmt.Errorf("undefined scalar %q", t.Name)
		}
		return v, nil, nil
	case *frontend.LenExpr:
		if a, ok := e.arrays[t.Name]; ok {
			return int64(len(a)), nil, nil
		}
		if a, ok := e.outputs[t.Name]; ok {
			return int64(len(a)), nil, nil
		}
		return 0, nil, fmt.Errorf("len of unknown array %q", t.Name)
	case *frontend.IndexExpr:
		idx, f, err := e.eval(t.Idx)
		if err != nil || f != nil {
			return 0, f, err
		}
		arr, ok := e.arrays[t.Name]
		if !ok {
			return 0, nil, fmt.Errorf("unknown input array %q", t.Name)
		}
		if idx < 0 || idx >= int64(len(arr)) {
			return 0, &refFault{
				code:   sem.FaultIndexOOB,
				detail: fmt.Sprintf("read %s[%d] len=%d", t.Name, idx, len(arr)),
			}, nil
		}
		return arr[idx], nil, nil
	case *frontend.UnaryExpr:
		if t.Op == "!" {
			v, f, err := e.eval(t.Inner)
			if err != nil || f != nil {
				return 0, f, err
			}
			return b2i(v == 0), nil, nil
		}
		v, f, err := e.eval(t.Inner)
		if err != nil || f != nil {
			return 0, f, err
		}
		switch t.Op {
		case "-":
			return -v, nil, nil
		case "~":
			return ^v, nil, nil
		}
		return 0, nil, fmt.Errorf("bad unary %q", t.Op)
	case *frontend.BinaryExpr:
		return e.evalBinary(t)
	}
	return 0, nil, fmt.Errorf("unsupported expr %T", x)
}

func (e *env) evalBinary(t *frontend.BinaryExpr) (int64, *refFault, error) {
	if t.Op == "&&" {
		l, f, err := e.eval(t.Left)
		if err != nil || f != nil {
			return 0, f, err
		}
		if l == 0 {
			return 0, nil, nil
		}
		r, f2, err := e.eval(t.Right)
		if err != nil || f2 != nil {
			return 0, f2, err
		}
		return b2i(r != 0), nil, nil
	}
	if t.Op == "||" {
		l, f, err := e.eval(t.Left)
		if err != nil || f != nil {
			return 0, f, err
		}
		if l != 0 {
			return 1, nil, nil
		}
		r, f2, err := e.eval(t.Right)
		if err != nil || f2 != nil {
			return 0, f2, err
		}
		return b2i(r != 0), nil, nil
	}
	l, f, err := e.eval(t.Left)
	if err != nil || f != nil {
		return 0, f, err
	}
	r, f2, err := e.eval(t.Right)
	if err != nil || f2 != nil {
		return 0, f2, err
	}
	if (t.Op == "/" || t.Op == "%") && r == 0 {
		return 0, &refFault{code: sem.FaultDivZero, detail: fmt.Sprintf("%s by zero", t.Op)}, nil
	}
	switch t.Op {
	case "+":
		return l + r, nil, nil
	case "-":
		return l - r, nil, nil
	case "*":
		return l * r, nil, nil
	case "/":
		return l / r, nil, nil
	case "%":
		return l % r, nil, nil
	case "&":
		return l & r, nil, nil
	case "|":
		return l | r, nil, nil
	case "^":
		return l ^ r, nil, nil
	case "==":
		return b2i(l == r), nil, nil
	case "!=":
		return b2i(l != r), nil, nil
	case "<":
		return b2i(l < r), nil, nil
	case ">":
		return b2i(l > r), nil, nil
	case "<=":
		return b2i(l <= r), nil, nil
	case ">=":
		return b2i(l >= r), nil, nil
	}
	return 0, nil, fmt.Errorf("bad op %q", t.Op)
}

func b2i(b bool) int64 {
	if b {
		return 1
	}
	return 0
}

func identity(op string) int64 {
	switch op {
	case "*":
		return 1
	default:
		return 0
	}
}

func applyRed(op string, acc, x int64) int64 {
	switch op {
	case "+":
		return acc + x
	case "*":
		return acc * x
	case "concat":
		return acc*10 + x
	}
	return acc
}
