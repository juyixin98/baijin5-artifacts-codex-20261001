package interp

import (
	"genfsm/internal/ast"
	"genfsm/internal/value"
)

// runBody executes the generator function on its own goroutine.
//
// Suspension is a blocking channel rendezvous in the scope-bound yield
// callback. Close injects a GeneratorExit exception at the yield site; user
// code catches it like any exception, and the structured recovery in execTry
// runs the applicable finally blocks. A second yield during that cleanup is
// reported as RuntimeError (yield-during-close), matching the VM.
func (g *Gen) runBody(sess *genSession) {
	g.execMu.Lock()
	defer g.execMu.Unlock()
	env := newScope(nil)
	for i, p := range g.fn.Params {
		var av value.Value = value.NullV()
		if i < len(g.args) {
			av = g.args[i]
		}
		env.define(p, av)
	}

	closeCleanup := false
	env.yield = func(v value.Value) (value.Value, bool) {
		if closeCleanup {
			return value.ExV("RuntimeError", "yield during generator close"), true
		}
		sess.outCh <- out{state: value.StateYielded, val: v}
		// Parked: no longer executing. Re-acquire execMu only when actually
		// resumed, so suspended generators can be driven but running ones
		// reject re-entry.
		g.execMu.Unlock()
		r := <-sess.reqCh
		g.execMu.Lock()
		switch r.kind {
		case reqSend:
			return r.val, true
		case reqNext:
			return value.NullV(), true
		case reqThrow:
			return r.val, true
		case reqClose:
			closeCleanup = true
			return value.ExV("GeneratorExit", ""), true
		}
		return value.NullV(), true
	}

	finish := func(o out) {
		select {
		case sess.outCh <- o:
		default:
		}
	}

	v, ex := g.in.execFuncBody(g.fn.Body, env)
	if ex.Ok() {
		if ex.name == "GeneratorExit" {
			finish(out{state: value.StateClosed})
			return
		}
		finish(out{state: value.StateError, ex: value.ExV(ex.name, ex.msg)})
		return
	}
	finish(out{state: value.StateFinished, val: v})
}

// execFor iterates a generator value. The child handle is driven directly,
// so generator nesting works on separate goroutines/channels. When the
// enclosing control flow leaves the loop, the child receives Close() once.
func (in *Interpreter) execFor(n *ast.ForStmt, env *scope) {
	iterV, ex := in.eval(n.Iter, env)
	if ex.Ok() {
		panic(&flowPanic{kind: flowThrow, ex: ex})
	}
	if iterV.Tag != value.Generator {
		panic(&flowPanic{kind: flowThrow, ex: exf("TypeError", "for-loop target is not a generator")})
	}
	child := iterV.Gen

	closed := false
	closeChild := func() {
		if closed {
			return
		}
		closed = true
		res := child.Close()
		if res.State == value.StateError {
			panic(&flowPanic{kind: flowThrow,
				ex: excVal{name: res.ExName, msg: res.ExMessage}})
		}
	}
	defer closeChild()

	for {
		res := child.Next()
		switch res.State {
		case value.StateYielded:
			benv := newScope(env)
			benv.define(n.Var, res.Value)
			in.execBlock(n.Body, benv)
		case value.StateFinished, value.StateClosed:
			closed = true
			return
		case value.StateError:
			panic(&flowPanic{kind: flowThrow,
				ex: excVal{name: res.ExName, msg: res.ExMessage}})
		}
	}
}
