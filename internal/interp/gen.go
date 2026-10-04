package interp

import (
	"sync"
	"sync/atomic"

	"genfsm/internal/ast"
	"genfsm/internal/semerr"
	"genfsm/internal/value"
)

// req is a driver -> generator message.
type req struct {
	kind reqKind
	val  value.Value
}

type reqKind int

const (
	reqNext reqKind = iota
	reqSend
	reqThrow
	reqClose
)

// out is a generator -> driver report.
type out struct {
	state value.GenState // suspended / finished / closed / error
	val   value.Value    // yielded or return value
	ex    value.Value    // exception when state == error
}

// genSession is the channel pair owned by one generator goroutine.
type genSession struct {
	reqCh chan req
	outCh chan out
	// closing is set by the body's yield hook when the driver requested close,
	// so a yield inside a finally-during-close is rejected.
	closing atomic.Bool
}

// Gen is the reference-engine generator handle (value.Gen).
type Gen struct {
	in   *Interpreter
	name string
	id   int64
	fn   *ast.Func
	args []value.Value

	mu      sync.Mutex
	status  value.GenStatus
	started bool
	sess    *genSession
	// execMu is held by the body goroutine for its whole lifetime. A
	// non-blocking acquire fails while the generator is executing, which is
	// exactly the "resume a running generator" condition (including re-entry
	// from user code on the same or another goroutine).
	execMu sync.Mutex
}

var genSeqRef atomic.Int64

func (in *Interpreter) spawnGen(fn *ast.Func, args []value.Value) *Gen {
	id := genSeqRef.Add(1)
	return &Gen{in: in, name: fn.Name, id: id, fn: fn, args: args, status: value.StatusNewborn}
}

func (g *Gen) GenName() string         { return g.name }
func (g *Gen) GenID() int64            { return g.id }
func (g *Gen) Status() value.GenStatus { g.mu.Lock(); s := g.status; g.mu.Unlock(); return s }


func (g *Gen) Next() value.Result {
	g.mu.Lock()
	if g.status == value.StatusRunning {
		g.mu.Unlock()
		return conflictRef(semerr.CodeResumeRunning, g.name)
	}
	if g.status == value.StatusFinished || g.status == value.StatusClosed {
		g.mu.Unlock()
		return stopIterRef(g.name)
	}
	newborn := g.status == value.StatusNewborn
	sess := g.ensureStartedLocked()
	g.status = value.StatusRunning
	g.mu.Unlock()

	if newborn {
		go g.runBody(sess)
	} else {
		sess.reqCh <- req{kind: reqNext}
	}
	return g.collect(sess)
}

func (g *Gen) Send(v value.Value) value.Result {
	g.mu.Lock()
	switch g.status {
	case value.StatusNewborn:
		g.mu.Unlock()
		return conflictRef(semerr.CodeSendNewborn, g.name)
	case value.StatusRunning:
		g.mu.Unlock()
		return conflictRef(semerr.CodeResumeRunning, g.name)
	case value.StatusFinished, value.StatusClosed:
		g.mu.Unlock()
		return stopIterRef(g.name)
	}
	sess := g.ensureStartedLocked()
	g.status = value.StatusRunning
	g.mu.Unlock()
	sess.reqCh <- req{kind: reqSend, val: v}
	return g.collect(sess)
}

func (g *Gen) Throw(ex value.Value) value.Result {
	g.mu.Lock()
	if g.status == value.StatusRunning {
		g.mu.Unlock()
		return conflictRef(semerr.CodeResumeRunning, g.name)
	}
	if g.status == value.StatusFinished || g.status == value.StatusClosed {
		g.mu.Unlock()
		return toErr(ex)
	}
	newborn := g.status == value.StatusNewborn
	sess := g.ensureStartedLocked()
	g.status = value.StatusRunning
	g.mu.Unlock()
	if newborn {
		// Throw into newborn: body never starts; surface immediately.
		g.markClosed()
		return toErr(ex)
	}
	sess.reqCh <- req{kind: reqThrow, val: ex}
	return g.collect(sess)
}

func (g *Gen) Close() value.Result {
	g.mu.Lock()
	switch g.status {
	case value.StatusFinished, value.StatusClosed:
		g.mu.Unlock()
		return value.Result{State: value.StateClosed}
	case value.StatusNewborn:
		// No code has run: no cleanup is applicable. Mark started so the body
		// can never execute later, and close once.
		g.started = true
		g.status = value.StatusClosed
		g.mu.Unlock()
		return value.Result{State: value.StateClosed}
	case value.StatusRunning:
		g.mu.Unlock()
		return conflictRef(semerr.CodeCloseRunning, g.name)
	}
	sess := g.ensureStartedLocked()
	g.status = value.StatusRunning
	g.mu.Unlock()
	sess.reqCh <- req{kind: reqClose}
	return g.collect(sess)
}

func (g *Gen) ensureStartedLocked() *genSession {
	if g.sess == nil {
		g.sess = &genSession{reqCh: make(chan req, 1), outCh: make(chan out, 1)}
	}
	return g.sess
}

// gateRunning detects re-entry of a generator that is currently executing.
// A suspended generator's body goroutine has released execMu (it is parked in
// the yield hook); a running generator still holds it. Returns true when it is
// safe to resume. The lock is released by the body goroutine when it parks
// again (yield) or terminates (collect observes a terminal report).
func (g *Gen) gateRunning() (value.Result, bool) {
	if !g.execMu.TryLock() {
		return conflictRef(semerr.CodeResumeRunning, g.name), false
	}
	return value.Result{}, true
}

// releaseGate drops the resume gate; called when a resumed body reports a
// terminal state, because the body goroutine unlocks execMu only on the park
// path (yield) or via the defer at runBody exit (terminal). On terminal exit
// the deferred Unlock already ran, so the gate is owned by nobody and we must
// not unlock here. We distinguish by terminal reports collected after the
// body goroutine has fully finished — tracked via sess.done.


func (g *Gen) markClosed() {
	g.mu.Lock()
	g.status = value.StatusClosed
	g.mu.Unlock()
}

func (g *Gen) collect(sess *genSession) value.Result {
	o := <-sess.outCh
	g.mu.Lock()
	switch o.state {
	case value.StateYielded:
		g.status = value.StatusSuspended
		g.mu.Unlock()
		return value.Result{State: value.StateYielded, Value: o.val}
	case value.StateFinished:
		g.status = value.StatusFinished
		g.mu.Unlock()
		return value.Result{State: value.StateFinished, Value: o.val}
	case value.StateClosed:
		g.status = value.StatusClosed
		g.mu.Unlock()
		return value.Result{State: value.StateClosed}
	default:
		// A yield-during-close failure still closes the generator; every
		// other terminal error finishes it.
		if o.ex.Tag == value.Exception && o.ex.ExName == "RuntimeError" &&
			o.ex.ExMessage == "yield during generator close" {
			g.status = value.StatusClosed
		} else {
			g.status = value.StatusFinished
		}
		g.mu.Unlock()
		return toErr(o.ex)
	}
}

func toErr(ex value.Value) value.Result {
	if ex.Tag != value.Exception {
		return value.Result{State: value.StateError,
			ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeBuiltinFail),
			ExName: "Error", ExMessage: ex.Display()}
	}
	kind, code := string(semerr.KindCompute), string(semerr.CodeUserRaised)
	if ex.ExName == "ResourceError" {
		kind, code = string(semerr.KindResource), string(semerr.CodeOutOfFuel)
	}
	if ex.ExName == "RuntimeError" && ex.ExMessage == "yield during generator close" {
		code = string(semerr.CodeYieldInClose)
	}
	return value.Result{State: value.StateError, ErrKind: kind, ErrCode: code,
		ExName: ex.ExName, ExMessage: ex.ExMessage}
}

func stopIterRef(name string) value.Result {
	return value.Result{State: value.StateFinished,
		ErrKind: string(semerr.KindCompute), ErrCode: string(semerr.CodeStopIter),
		ErrMsg: "generator " + name + " is exhausted"}
}

func conflictRef(code semerr.Code, name string) value.Result {
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
