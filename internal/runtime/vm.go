package runtime

import (
	"fmt"
	"strconv"

	"scopelang/internal/diag"
	"scopelang/internal/ir"
)

// VM executes a lowered ir.Program. Runtime state mirrors the lowering:
//
//	guards[] - one bool per statically acquired resource slot; the cleanup
//	           section tests/clears it, so the shared unwind path cannot run
//	           a destructor twice;
//	temps[]  - repeat remaining-iteration counters;
//	reason   - current unwind mode as control flows through cleanup sections;
//	retVal   - value carried by an explicit return.
//
// Failures never raise Go errors: they update the shared Policy and the
// reason register and the program keeps unwinding.
type VM struct {
	prog   *ir.Program
	host   *Host
	policy *Policy
	steps  []Step

	guards []bool
	temps  []int
	reason string
	retVal string
	ip     int
	loopInstance []int // 1-based iteration per enclosing loop at runtime
}

func RunVM(prog *ir.Program, spec *RunSpec) *Outcome {
	vm := &VM{
		prog: prog, host: NewHost(spec), policy: &Policy{},
		guards: make([]bool, prog.Slots), temps: make([]int, prog.Temps),
		reason: reasonNormal,
	}
	status, failAt := vm.run()
	out := &Outcome{
		RunID: spec.ID, Engine: "vm", Status: status, ReturnVal: vm.retVal,
		Error: vm.policy.Primary(), Events: vm.host.Trace(), Steps: vm.steps,
	}
	if status == StatusFail && out.Error == nil {
		out.Error = diag.New(diag.Internal, "VM_NO_ERROR",
			"fail status without recorded error at ip "+strconv.Itoa(failAt)).
			With(diag.PhaseRuntime, "", 0)
	}
	return out
}

func (vm *VM) run() (Status, int) {
	ins := vm.prog.Instructions
	for vm.ip < len(ins) {
		in := ins[vm.ip]
		switch in.Op {
		case ir.OpEmit:
			vm.host.Emit(in.Text)
			vm.note(in, "emit")
			vm.ip++
		case ir.OpAcquire:
			instance := vm.instanceForPoint(in.Point)
			err := vm.host.Acquire(in.Point, in.Name, in.Expr, instance)
			if err != nil {
				vm.reason = vm.policy.Observe(err, vm.reason)
				vm.noteAt(in, instance, "acquire failed")
				vm.jump(in.Target)
				continue
			}
			vm.guards[in.Slot] = true
			vm.noteAt(in, instance, "acquired")
			vm.ip++
		case ir.OpUnwind:
			vm.recordUnwind(in)
			vm.jump(in.Target)
		case ir.OpCleanupFail:
			instance := vm.instanceForPoint(in.Point)
			err := vm.host.CleanupError(in.Point, instance)
			vm.reason = vm.policy.Observe(err, vm.reason)
			vm.noteAt(in, instance, "cleanup fail")
			vm.ip++
		case ir.OpSetCounter:
			vm.temps[in.Temp] = in.Count
			vm.ip++
		case ir.OpIterEnter:
			iter := in.Count - vm.temps[in.Temp] + 1
			vm.loopInstance = append(vm.loopInstance, iter)
			vm.ip++
		case ir.OpIterLeave:
			if len(vm.loopInstance) > 0 {
				vm.loopInstance = vm.loopInstance[:len(vm.loopInstance)-1]
			}
			vm.ip++
		case ir.OpCounterCheck:
			if vm.temps[in.Temp] <= 0 {
				vm.jump(in.Target)
				continue
			}
			vm.ip++
		case ir.OpCounterDecr:
			vm.temps[in.Temp]--
			vm.ip++
		case ir.OpResetGuards:
			for _, slot := range in.Slots {
				vm.guards[slot] = false
			}
			vm.ip++
		case ir.OpGuardCheck:
			if !vm.guards[in.Slot] {
				vm.jump(in.Target)
				continue
			}
			vm.ip++
		case ir.OpGuardClear:
			vm.guards[in.Slot] = false
			vm.ip++
		case ir.OpJmp:
			vm.jump(in.Target)
		case ir.OpRoute:
			vm.route(in.Normal, in.Break, in.Return, in.Fail)
		case ir.OpBreakAbsorb:
			vm.breakAbsorb(in.Normal, in.Break, in.Return, in.Fail)
		case ir.OpHaltOK:
			return StatusOK, vm.ip
		case ir.OpHaltReturn:
			return StatusReturn, vm.ip
		case ir.OpHaltFail:
			return StatusFail, vm.ip
		default:
			err := diag.New(diag.Internal, "VM_BAD_OP", "unknown op "+string(in.Op)).
				With(diag.PhaseRuntime, "", 0)
			vm.reason = vm.policy.Observe(err, vm.reason)
			return StatusFail, vm.ip
		}
	}
	err := diag.New(diag.Internal, "VM_RAN_OFF", "instruction stream ended without halt").
		With(diag.PhaseRuntime, "", 0)
	vm.reason = vm.policy.Observe(err, vm.reason)
	return StatusFail, vm.ip
}

func (vm *VM) recordUnwind(in ir.Instruction) {
	switch in.Reason {
	case ir.RFail:
		instance := vm.instanceForPoint(in.Point)
		err := vm.host.ExplicitFail(in.Point, instance)
		vm.reason = vm.policy.Observe(err, vm.reason)
		vm.noteAt(in, instance, "explicit fail")
	case ir.RBreak:
		vm.reason = reasonBreak
		vm.note(in, "break")
	case ir.RReturn:
		vm.reason = reasonReturn
		vm.retVal = in.Value
		vm.note(in, "return")
	}
}

func (vm *VM) route(normal, brk, ret, fail string) {
	switch vm.reason {
	case reasonBreak:
		vm.jump(brk)
	case reasonReturn:
		vm.jump(ret)
	case reasonFail:
		vm.jump(fail)
	default:
		vm.jump(normal)
	}
}

// breakAbsorb is emitted at a repeat-loop body cleanup boundary: a break that
// survived the iteration cleanups is consumed here. A cleanup error would
// already have promoted the reason to fail, so at this point break is clean.
func (vm *VM) breakAbsorb(normal, after, ret, fail string) {
	switch vm.reason {
	case reasonBreak:
		vm.reason = reasonNormal
		vm.jump(after)
	case reasonReturn:
		vm.jump(ret)
	case reasonFail:
		vm.jump(fail)
	default:
		vm.jump(normal)
	}
}

func (vm *VM) jump(target string) {
	n, err := strconv.Atoi(target)
	if err != nil {
		panic(fmt.Sprintf("unresolved label %q", target))
	}
	vm.ip = n
}

// instanceForPoint returns the 1-based repeat iteration for in-loop points.
// Loop nesting depth is derived from loopInstance stack maintained by an
// external convention; in this language loops cannot nest syntactically
// inside another loop's direct body without an intervening scope, and the
// lowering tracks repeats through set_counter regions. We derive the value
// simply from the innermost active iteration counter region recorded on
// steps: a point is in-loop when the compiler marked it so via PointInfo.
func (vm *VM) instanceForPoint(point string) int {
	if !vm.pointInLoop(point) {
		return 0
	}
	if len(vm.loopInstance) == 0 {
		return 1
	}
	return vm.loopInstance[len(vm.loopInstance)-1]
}

func (vm *VM) pointInLoop(point string) bool {
	for _, pi := range vm.prog.Points {
		if pi.Point == point {
			return pi.InRepeat
		}
	}
	return false
}

func (vm *VM) note(in ir.Instruction, n string) {
	vm.steps = append(vm.steps, Step{
		IP: vm.ip, Op: string(in.Op), Reason: vm.reason, Line: in.Line,
		Point: in.Point, Note: n, Guards: snapGuards(vm.guards),
	})
}

func (vm *VM) noteAt(in ir.Instruction, instance int, n string) {
	vm.steps = append(vm.steps, Step{
		IP: vm.ip, Op: string(in.Op), Reason: vm.reason, Line: in.Line,
		Point: in.Point, Instance: instance, Note: n, Guards: snapGuards(vm.guards),
	})
}

func snapGuards(g []bool) []bool {
	out := make([]bool, len(g))
	copy(out, g)
	return out
}
