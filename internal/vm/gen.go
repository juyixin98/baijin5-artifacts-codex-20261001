package vm

import (
	"sync"
	"fmt"
	"genfsm/internal/semerr"
	"genfsm/internal/value"
)

// Gen is the VM-side generator handle implementing value.Gen.
type Gen struct {
	m      *Machine
	f      *frame
	name   string
	id     int64
	mu     sync.Mutex
	status value.GenStatus
}

func (m *Machine) newGen(f *frame) *Gen {
	id := m.genSeq.Add(1)
	return &Gen{m: m, f: f, name: f.r.Name, id: id, status: value.StatusNewborn}
}

func (g *Gen) GenName() string         { return g.name }
func (g *Gen) GenID() int64            { return g.id }
// Status returns a best-effort lifecycle snapshot. It never blocks: when the
// handle is mid-execution (lock held), TryLock fails and the observed state
// is "running", which is exactly the truth at that instant.
func (g *Gen) Status() value.GenStatus {
	if !g.mu.TryLock() {
		return value.StatusRunning
	}
	s := g.status
	g.mu.Unlock()
	return s
}


func exResult(ex value.Value) value.Result {
	if ex.Tag == value.Exception {
		return value.Result{State: value.StateError,
			ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeUserRaised),
			ExName: ex.ExName, ExMessage: ex.ExMessage}
	}
	return value.Result{State: value.StateError,
		ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeBuiltinFail),
		ExName: "Error", ExMessage: ex.Display()}
}

// enter transitions a handle into running under the lock. It returns the
// prior status; ok=false when the action is illegal for that status, with
// the failure result already produced.
func (g *Gen) enter(action string) (value.GenStatus, value.Result, bool) {
	// TryLock: a handle that is currently executing on this call stack (or
	// driven concurrently from another goroutine) is "already running". A
	// blocking Lock here would self-deadlock on legal-looking user code that
	// re-enters a running generator, so we classify it immediately.
	if !g.mu.TryLock() {
		return value.StatusRunning, conflict(semerr.CodeResumeRunning, g.name, action), false
	}
	st := g.status
	switch action {
	case "next":
		switch st {
		case value.StatusRunning:
			g.mu.Unlock()
			return st, conflict(semerr.CodeResumeRunning, g.name, "next"), false
		case value.StatusFinished, value.StatusClosed:
			g.mu.Unlock()
			return st, stopIter(g.name), false
		}
	case "send":
		switch st {
		case value.StatusNewborn:
			g.mu.Unlock()
			return st, conflict(semerr.CodeSendNewborn, g.name, "send"), false
		case value.StatusRunning:
			g.mu.Unlock()
			return st, conflict(semerr.CodeResumeRunning, g.name, "send"), false
		case value.StatusFinished, value.StatusClosed:
			g.mu.Unlock()
			return st, stopIter(g.name), false
		}
	case "throw":
		if st == value.StatusRunning {
			g.mu.Unlock()
			return st, conflict(semerr.CodeResumeRunning, g.name, "throw"), false
		}
		if st == value.StatusFinished || st == value.StatusClosed {
			g.mu.Unlock()
			return st, value.Result{}, false // caller fills exception
		}
	case "close":
		switch st {
		case value.StatusFinished, value.StatusClosed:
			g.mu.Unlock()
			return st, value.Result{State: value.StateClosed}, false
		case value.StatusNewborn:
			g.status = value.StatusClosed
			g.mu.Unlock()
			return st, value.Result{State: value.StateClosed}, false
		case value.StatusRunning:
			g.mu.Unlock()
			return st, conflict(semerr.CodeCloseRunning, g.name, "close"), false
		}
	}
	g.status = value.StatusRunning
	return st, value.Result{}, true
}

// finish records the post-run status and releases the drive lock.
func (g *Gen) finish(res frameResult, in *resumeIn) value.Result {
	switch res.kind {
	case sigYield:
		g.status = value.StatusSuspended
		g.mu.Unlock()
		return value.Result{State: value.StateYielded, Value: res.val}
	case sigReturn:
		g.status = value.StatusFinished
		g.mu.Unlock()
		return value.Result{State: value.StateFinished, Value: res.val}
	case sigClose:
		g.status = value.StatusClosed
		g.mu.Unlock()
		return value.Result{State: value.StateClosed}
	case sigFatal:
		// A re-entrancy conflict reached through user code aborts the resume
		// with the original CONFLICT category. The generator is finished.
		g.status = value.StatusFinished
		g.mu.Unlock()
		return value.Result{
			State:   value.StateError,
			ErrKind: res.fatalKind,
			ErrCode: res.fatalCode,
			ErrMsg:  res.fatalMsg,
		}
	case sigThrow:
		if in != nil && in.closing && res.ex.ExName == "RuntimeError" {
			g.status = value.StatusClosed
			g.mu.Unlock()
			return value.Result{State: value.StateError,
				ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeYieldInClose),
				ExName: res.ex.ExName, ExMessage: res.ex.ExMessage}
		}
		if in != nil && in.closing {
			g.status = value.StatusClosed
			g.mu.Unlock()
			return value.Result{State: value.StateClosed}
		}
		g.status = value.StatusFinished
		g.mu.Unlock()
		if isResource(res.ex) {
			return value.Result{State: value.StateError,
				ErrKind: string(semerr.KindResource), ErrCode: string(semerr.CodeOutOfFuel),
				ExName: res.ex.ExName, ExMessage: res.ex.ExMessage}
		}
		return exResult(res.ex)
	}
	g.status = value.StatusFinished
	g.mu.Unlock()
	return value.Result{State: value.StateError,
		ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeBuiltinFail)}
}

func (g *Gen) runLocked(in *resumeIn) value.Result {
	if in != nil {
		g.f.resume = in
	}
	depth := 1
	res := g.m.runWith(g.f, &depth)
	g.f.resume = nil
	return g.finish(res, in)
}

// Next resumes with no injection. Newborn generators start normally.
func (g *Gen) Next() value.Result {
	if _, res, ok := g.enter("next"); !ok {
		return res
	}
	return g.runLocked(nil)
}

// Send resumes injecting a value; illegal on a newborn generator.
func (g *Gen) Send(v value.Value) value.Result {
	if _, res, ok := g.enter("send"); !ok {
		return res
	}
	return g.runLocked(&resumeIn{val: v})
}

// Throw resumes injecting an exception at the yield site.
func (g *Gen) Throw(ex value.Value) value.Result {
	st, _, ok := g.enter("throw")
	if !ok {
		if st == value.StatusFinished || st == value.StatusClosed {
			return exResult(ex)
		}
		return conflict(semerr.CodeResumeRunning, g.name, "throw")
	}
	return g.runLocked(&resumeIn{val: ex, throwIn: true})
}

// Close is idempotent and runs applicable cleanup exactly once.
func (g *Gen) Close() value.Result {
	if _, res, ok := g.enter("close"); !ok {
		return res
	}
	return g.runLocked(&resumeIn{throwIn: true, closing: true})
}

func conflict(code semerr.Code, name, op string) value.Result {
	msg := ""
	switch code {
	case semerr.CodeResumeRunning:
		msg = "cannot resume running generator " + name
	case semerr.CodeCloseRunning:
		msg = "cannot close running generator " + name
	case semerr.CodeSendNewborn:
		msg = "cannot send into newborn generator " + name + "; call next() first"
	}
	return value.Result{State: value.StateError,
		ErrKind: string(semerr.KindConflict), ErrCode: string(code), ErrMsg: msg}
}

func stopIter(name string) value.Result {
	return value.Result{State: value.StateFinished,
		ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeStopIter),
		ErrMsg: "generator " + name + " is exhausted"}
}

func isResource(ex value.Value) bool {
	return ex.Tag == value.Exception && ex.ExName == "ResourceError"
}

// Dump returns a debug string of the frame state.
func Dump(g *Gen) string {
	f := g.f
	parts := fmt.Sprintf("status=%s pc=%d stack=[", g.status, f.pc)
	for _, v := range f.stack {
		parts += " " + v.Display()
	}
	parts += " ] locals=["
	for i, v := range f.locals {
		parts += fmt.Sprintf(" %d:%s", i, v.Display())
	}
	return parts + " ]"
}
