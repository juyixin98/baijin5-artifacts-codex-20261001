package runtime

// Control-flow signals realized by unwinding the Go stack:
type sigReturn struct {
	has   bool
	value Value
}
type sigClosing struct{}

// dRaised is a catchable user exception.
type dRaised struct{ value Value }

func (e dRaised) Error() string { return "raised: " + e.value.S }

// dFailure models a built-in uncatchable compute failure (div0/type). It
// still flows through defers, but catch clauses never match it.
type dFailure struct {
	code string
	msg  string
}

func (e dFailure) Error() string { return e.code + ": " + e.msg }

type dEnv struct {
	d      *Direct
	locals map[string]Value
	logs   []string
	wire   *wire // wire for the request currently being serviced
}

func (d *Direct) runner() {
	// Initial park: wait for the first request.

	first := newWire()
	d.park(first)
	close(d.ready)
	<-first.ack

	env := &dEnv{d: d, locals: map[string]Value{}, wire: first}
	out := env.serve(first.req)

	// On a terminal outcome the same wire that delivered the last yield is
	// reused; if the generator never yielded, env.wire is still `first`.
	env.wire.finish <- out
}

func newWire() *wire {
	return &wire{ack: make(chan struct{}), finish: make(chan Outcome, 1)}
}

func (d *Direct) park(w *wire) { d.install(w) }

func (d *Direct) install(w *wire) {
	d.mu.Lock()
	d.parked = true
	d.current = w
	d.mu.Unlock()
}

// parkAtYield delivers a Suspended outcome on the current wire, then installs
// and waits for the next one. The returned request resumes the Go stack.
func (env *dEnv) parkAtYield(v Value) dRequest {
	out := Outcome{Kind: OutYielded, Value: ptrVal(v), State: Suspended, Logs: env.logs}
	env.logs = nil
	env.wire.finish <- out

	nxt := newWire()
	env.d.install(nxt)
	<-nxt.ack
	env.d.mu.Lock()
	env.d.parked = false
	env.d.mu.Unlock()
	env.wire = nxt
	return nxt.req
}

func (env *dEnv) serve(req dRequest) (out Outcome) {
	defer func() {
		switch r := recover().(type) {
		case nil:
			out = Outcome{Kind: OutExhausted, State: Done, Logs: env.logs}
		case sigReturn:
			out = Outcome{Kind: OutExhausted, State: Done, Logs: env.logs}
		case sigClosing:
			out = Outcome{Kind: OutClosedOK, State: Closed, Logs: env.logs}
		case dRaised:
			out = Outcome{Kind: OutFailed, State: Failed, Logs: env.logs,
				ErrCode: "user_exception", ErrMsg: "uncaught exception " + quote(r.value.S)}
		case dFailure:
			out = Outcome{Kind: OutFailed, State: Failed, Logs: env.logs,
				ErrCode: r.code, ErrMsg: r.msg}
		default:
			panic(r)
		}
	}()

	switch req.kind {
	case reqNext:
		env.execList(env.d.prog.Body)
	case reqThrow:
		panic(dRaised{value: req.inject})
	case reqClose:
		panic(sigClosing{})
	}
	return out
}

func quote(s string) string { return "\"" + s + "\"" }
func ptrVal(v Value) *Value { vv := v; return &vv }
