// Package runtime executes a lowered masked-SIMD ir.Program.
//
// Hard guarantees enforced here:
//   - masked lanes never index memory and never execute / or %;
//   - the final, possibly short batch runs under an explicit valid-lane mask;
//   - stores are committed only up to (and excluding) the earliest trapping
//     (global index, statement) pair, matching scalar prefix execution.
package runtime

import (
	"fmt"

	"simdc/internal/ir"
	"simdc/internal/sem"
)

// Inputs bundles request data.
type Inputs struct {
	Scalars map[string]int64
	Arrays  map[string][]int64
	// OutputArrayLen gives the declared length for each array output.
	OutputArrayLen map[string]int
	OutputScalars  []string // declared scalar output names
	// IterCount is the resolved exclusive loop trip count.
	IterCount int64
}

type event struct {
	gi     int
	stmtID int
	stage  sem.Stage
	code   sem.FaultCode
	detail string
}

type pendingStore struct {
	gi     int
	stmtID int
	name   string
	idx    int64
	val    int64
}

// Run interprets the IR and returns the observable result.
func Run(p *ir.Program, in Inputs) (*sem.Result, error) {
	w := p.Width
	outArrays := map[string][]int64{}
	for name, n := range in.OutputArrayLen {
		outArrays[name] = make([]int64, n)
	}
	outScalars := map[string]int64{}
	for _, name := range in.OutputScalars {
		outScalars[name] = 0
	}

	var halt *event

	// Process body batches.
	for base := 0; int64(base) < in.IterCount; base += w {
		live := make([]bool, w)
		for lane := 0; lane < w; lane++ {
			live[lane] = int64(base+lane) < in.IterCount
		}
		regs := make([][]int64, p.NumRegs)
		masks := make([][]bool, p.NumMasks)
		for i := range regs {
			regs[i] = make([]int64, w)
		}
		for i := range masks {
			masks[i] = make([]bool, w)
		}
		var events []event
		var stores []pendingStore

		getM := func(id ir.Mask) []bool { return masks[int(id)] }
		for _, ins := range p.Body {
			switch ins.Op {
			case ir.OpMaskTail:
				for lane := 0; lane < w; lane++ {
					masks[int(ins.MDst)][lane] = live[lane] && int64(lane) < ins.Imm-int64(base)
				}
			case ir.OpConst:
				for lane := 0; lane < w; lane++ {
					regs[int(ins.Dst)][lane] = ins.Imm
				}
			case ir.OpIVar:
				for lane := 0; lane < w; lane++ {
					regs[int(ins.Dst)][lane] = int64(base + lane)
				}
			case ir.OpLoadS:
				v, ok := in.Scalars[ins.Name]
				if !ok {
					if ov, ok2 := outScalars[ins.Name]; ok2 {
						v = ov
					} else {
						return nil, fmt.Errorf("missing scalar input %q", ins.Name)
					}
				}
				for lane := 0; lane < w; lane++ {
					regs[int(ins.Dst)][lane] = v
				}
			case ir.OpLoad:
				arr, ok := in.Arrays[ins.Name]
				if !ok {
					return nil, fmt.Errorf("missing input array %q", ins.Name)
				}
				m := getM(ins.Mask)
				for lane := 0; lane < w; lane++ {
					if !live[lane] || !m[lane] {
						continue // masked lane: do not touch memory
					}
					idx := regs[int(ins.A)][lane]
					if idx < 0 || idx >= int64(len(arr)) {
						events = append(events, event{
							gi: base + lane, stmtID: ins.StmtID, stage: sem.StageBody,
							code:   sem.FaultIndexOOB,
							detail: fmt.Sprintf("read %s[%d] len=%d", ins.Name, idx, len(arr)),
						})
						continue
					}
					regs[int(ins.Dst)][lane] = arr[idx]
				}
			case ir.OpBin, ir.OpCmp:
				m := getM(ins.Mask)
				for lane := 0; lane < w; lane++ {
					if !live[lane] || !m[lane] {
						continue
					}
					a := regs[int(ins.A)][lane]
					b := regs[int(ins.B)][lane]
					if ins.Op == ir.OpBin && (ins.Bop == ir.Quo || ins.Bop == ir.Rem) {
						if b == 0 {
							events = append(events, event{
								gi: base + lane, stmtID: ins.StmtID, stage: sem.StageBody,
								code:   sem.FaultDivZero,
								detail: fmt.Sprintf("%s by zero at i=%d", ins.Bop, base+lane),
							})
							continue
						}
					}
					if ins.Op == ir.OpCmp {
						masks[int(ins.MDst)][lane] = compare(ins.Cop, a, b)
						continue
					}
					regs[int(ins.Dst)][lane] = binop(ins.Bop, a, b, m[lane])
				}
			case ir.OpAnd:
				x := getM(ins.Mask)
				y := getM(ir.Mask(ins.Imm))
				for lane := 0; lane < w; lane++ {
					masks[int(ins.MDst)][lane] = live[lane] && x[lane] && y[lane]
				}
			case ir.OpNot:
				x := getM(ins.Mask)
				for lane := 0; lane < w; lane++ {
					masks[int(ins.MDst)][lane] = live[lane] && !x[lane]
				}
			case ir.OpStore:
				m := getM(ins.Mask)
				dst := outArrays[ins.Name]
				for lane := 0; lane < w; lane++ {
					if !live[lane] || !m[lane] {
						continue
					}
					idx := regs[int(ins.A)][lane]
					if idx < 0 || idx >= int64(len(dst)) {
						events = append(events, event{
							gi: base + lane, stmtID: ins.StmtID, stage: sem.StageBody,
							code:   sem.FaultOutputOOB,
							detail: fmt.Sprintf("write %s[%d] len=%d", ins.Name, idx, len(dst)),
						})
						continue
					}
					stores = append(stores, pendingStore{
						gi: base + lane, stmtID: ins.StmtID, name: ins.Name,
						idx: idx, val: regs[int(ins.B)][lane],
					})
				}
			case ir.OpReduce:
				// reductions run in the dedicated ascending pass below
			}
		}

		// Earliest event by (gi, stmtID).
		first := earliest(events)
		if first != nil {
			halt = first
			commitStores(stores, outArrays, int64(first.gi), first.stmtID)
			break
		}
		commitStores(stores, outArrays, in.IterCount, 1<<30)
	}

	// Reduce pass only if the body completed without a fault.
	if halt == nil {
		for _, r := range p.Reduces {
			arr := in.Arrays[r.Source]
			acc := reduceIdentity(r.Rop)
			var rf *event
			for i := int64(0); i < in.IterCount; i++ {
				if i < 0 || i >= int64(len(arr)) {
					rf = &event{
						gi: int(i), stmtID: -1, stage: sem.StageReduce,
						code:   sem.FaultReduceOOB,
						detail: fmt.Sprintf("reduce read %s[%d] len=%d", r.Source, i, len(arr)),
					}
					break
				}
				acc = applyReduce(r.Rop, acc, arr[i])
			}
			if rf != nil {
				halt = rf
				break
			}
			outScalars[r.Target] = acc
		}
	}

	res := &sem.Result{
		Halted: halt != nil,
		Outputs: sem.Outputs{
			Scalars: outScalars,
			Arrays:  outArrays,
		},
	}
	if halt != nil {
		res.Fault = &sem.Fault{
			Code: halt.code, GlobalIdx: halt.gi, StmtID: halt.stmtID,
			Stage: halt.stage, Detail: halt.detail,
		}
	}
	return res, nil
}

func commitStores(stores []pendingStore, dst map[string][]int64, cutGI int64, cutStmt int) {
	for _, s := range stores {
		if int64(s.gi) < cutGI || (int64(s.gi) == cutGI && s.stmtID < cutStmt) {
			dst[s.name][s.idx] = s.val
		}
	}
}

func earliest(ev []event) *event {
	if len(ev) == 0 {
		return nil
	}
	best := 0
	for i := 1; i < len(ev); i++ {
		if ev[i].gi < ev[best].gi || (ev[i].gi == ev[best].gi && ev[i].stmtID < ev[best].stmtID) {
			best = i
		}
	}
	return &ev[best]
}

func compare(op ir.CmpOp, a, b int64) bool {
	switch op {
	case ir.Eq:
		return a == b
	case ir.Neq:
		return a != b
	case ir.Lt:
		return a < b
	case ir.Gt:
		return a > b
	case ir.Le:
		return a <= b
	case ir.Ge:
		return a >= b
	}
	return false
}

func binop(op ir.BinOp, a, b int64, active bool) int64 {
	if op == ir.Sel {
		if active {
			return a
		}
		return b
	}
	switch op {
	case ir.Add:
		return a + b
	case ir.Sub:
		return a - b
	case ir.Mul:
		return a * b
	case ir.Quo:
		return a / b
	case ir.Rem:
		return a % b
	case ir.And:
		return a & b
	case ir.Or:
		return a | b
	case ir.Xor:
		return a ^ b
	}
	return 0
}

func reduceIdentity(op string) int64 {
	switch op {
	case "+", "concat":
		return 0
	case "*":
		return 1
	}
	return 0
}

func applyReduce(op string, acc, x int64) int64 {
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
