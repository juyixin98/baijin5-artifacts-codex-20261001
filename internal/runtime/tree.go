package runtime

import (
	"scopelang/internal/ast"
)

// TreeEngine is the reference AST interpreter. Cleanup is explicit runtime
// bookkeeping: every lexical scope has a frame holding its successfully
// acquired resources; scope exit releases them in reverse acquisition order.
//
// A signal (break/return/fail) propagates frame by frame. Plain frames run
// cleanups and forward the signal; the repeat-loop frame absorbs break into
// normal continuation after iteration cleanups. The same Host and fixed
// Policy are shared with the IR VM.
type TreeEngine struct {
	host          *Host
	policy        *Policy
	steps         []Step
	instanceStack []int // current 1-based repeat iteration per enclosing loop
}

type tFrame struct {
	loop     bool
	acquired []*tRes
}

type tRes struct {
	name     string
	point    string
	instance int
	node     *ast.Acquire
}

type unwindKind int

const (
	unwindNone unwindKind = iota
	unwindBreak
	unwindReturn
	unwindFail
)

type signal struct {
	kind  unwindKind
	value string
}

func RunTree(root *ast.Root, spec *RunSpec) *Outcome {
	e := &TreeEngine{host: NewHost(spec), policy: &Policy{}}
	sig := e.execStmts(root.Body.Stmts, nil)
	status := StatusOK
	retVal := ""
	switch sig.kind {
	case unwindReturn:
		status = StatusReturn
		retVal = sig.value
	case unwindFail:
		status = StatusFail
	case unwindBreak:
		// break at program top is rejected by the frontend validator.
		status = StatusFail
	case unwindNone:
		status = StatusOK
	}
	return &Outcome{
		RunID: spec.ID, Engine: "tree", Status: status, ReturnVal: retVal,
		Error: e.policy.Primary(), Events: e.host.Trace(), Steps: e.steps,
	}
}

func (e *TreeEngine) note(s Step) { e.steps = append(e.steps, s) }

func (e *TreeEngine) instance() int {
	if len(e.instanceStack) == 0 {
		return 0
	}
	return e.instanceStack[len(e.instanceStack)-1]
}

// execStmts runs statements with the given enclosing frames (nearest last).
// It returns the terminal signal, having cleaned exactly the frames created
// inside this statement sequence.
func (e *TreeEngine) execStmts(stmts []ast.Stmt, parents []*tFrame) signal {
	fr := &tFrame{}
	frames := append(parents, fr)
	for _, s := range stmts {
		if sig, done := e.execOne(s, frames); done {
			sig = e.cleanup(fr, sig)
			return sig
		}
	}
	return e.cleanup(fr, signal{})
}

func (e *TreeEngine) execOne(s ast.Stmt, frames []*tFrame) (signal, bool) {
	fr := frames[len(frames)-1]
	switch n := s.(type) {
	case *ast.Acquire:
		instance := e.instance()
		if err := e.host.Acquire(n.Point, n.Name, n.ResExpr, instance); err != nil {
			reason := e.policy.Observe(err, reasonNormal)
			e.note(Step{Line: n.Line, Point: n.Point, Instance: instance,
				Reason: reason, Note: "acquire failed"})
			return signal{kind: unwindFail}, true
		}
		fr.acquired = append(fr.acquired, &tRes{
			name: n.Name, point: n.Point, instance: instance, node: n,
		})
		e.note(Step{Line: n.Line, Point: n.Point, Instance: instance, Note: "acquired"})
		return signal{}, false
	case *ast.Emit:
		e.host.Emit(n.Text)
		e.note(Step{Line: n.Line, Note: "emit " + n.Text})
		return signal{}, false
	case *ast.Fail:
		instance := e.instance()
		err := e.host.ExplicitFail(n.Point, instance)
		reason := e.policy.Observe(err, reasonNormal)
		e.note(Step{Line: n.Line, Point: n.Point, Instance: instance,
			Reason: reason, Note: "explicit fail"})
		return signal{kind: unwindFail}, true
	case *ast.Return:
		e.note(Step{Line: n.Line, Reason: reasonReturn, Note: "return"})
		return signal{kind: unwindReturn, value: n.Value}, true
	case *ast.Break:
		e.note(Step{Line: n.Line, Reason: reasonBreak, Note: "break"})
		return signal{kind: unwindBreak}, true
	case *ast.Block:
		// execStmts creates and cleans the inner frame; forward any signal.
		sig := e.execStmts(n.Stmts, frames)
		if sig.kind == unwindNone {
			return signal{}, false
		}
		return sig, true
	case *ast.Repeat:
		return e.execRepeat(n, frames), true
	}
	return signal{}, false
}

func (e *TreeEngine) execRepeat(r *ast.Repeat, parents []*tFrame) signal {
	for iteration := 1; iteration <= r.Count; iteration++ {
		e.instanceStack = append(e.instanceStack, iteration)
		fr := &tFrame{loop: true}
		frames := append(parents, fr)
		sig := signal{}
		stopped := false
		for _, s := range r.Body.Stmts {
			if inner, done := e.execOne(s, frames); done {
				sig = inner
				stopped = true
				break
			}
		}
		_ = stopped
		sig = e.cleanup(fr, sig)
		e.instanceStack = e.instanceStack[:len(e.instanceStack)-1]
		switch sig.kind {
		case unwindBreak:
			return signal{} // loop absorbs break into normal flow
		case unwindReturn, unwindFail:
			return sig
		}
	}
	return signal{}
}

// cleanup releases a frame's resources in reverse order and applies the
// fixed cleanup-error policy. The signal returned reflects any promotion
// (normal/break -> fail) but keeps break/return identity otherwise.
func (e *TreeEngine) cleanup(fr *tFrame, sig signal) signal {
	reason := reasonName(sig.kind)
	for i := len(fr.acquired) - 1; i >= 0; i-- {
		res := fr.acquired[i]
		reason = e.runCleanup(res, reason)
	}
	switch reason {
	case reasonFail:
		sig.kind = unwindFail
	case reasonReturn:
		sig.kind = unwindReturn
	case reasonBreak:
		sig.kind = unwindBreak
	default:
		sig.kind = unwindNone
	}
	return sig
}

func (e *TreeEngine) runCleanup(res *tRes, reason string) string {
	if err := e.host.Release(res.point, res.name, res.instance); err != nil {
		return e.policy.Observe(err, reason)
	}
	for _, cs := range res.node.Cleanup.Stmts {
		switch cn := cs.(type) {
		case *ast.Emit:
			e.host.Emit(cn.Text)
		case *ast.Fail:
			err := e.host.CleanupError(cn.Point, res.instance)
			reason = e.policy.Observe(err, reason)
			e.note(Step{Line: cn.Line, Point: cn.Point, Instance: res.instance,
				Reason: reason, Note: "cleanup fail"})
			return reason
		}
	}
	return reason
}

func reasonName(k unwindKind) string {
	switch k {
	case unwindBreak:
		return reasonBreak
	case unwindReturn:
		return reasonReturn
	case unwindFail:
		return reasonFail
	default:
		return reasonNormal
	}
}
