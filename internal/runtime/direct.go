package runtime

import (
	"sync"

	"genstatemachine/internal/frontend"
)

// wire is a per-request synchronous handshake channel.
type wire struct {
	req    dRequest
	ack    chan struct{} // driver closes to deliver the request
	finish chan Outcome  // goroutine sends one outcome when settled
}

// Direct is the reference interpreter. A dedicated goroutine runs the AST
// using Go's own stack and defer/recover for try/catch/finally; at each yield
// it parks on a channel, so locals and pending defers are held alive by the
// Go runtime rather than by any explicit state. It never consults the IR.
type Direct struct {
	prog *frontend.GenDecl

	mu      sync.Mutex
	state   State
	parked  bool
	current *wire
	ready   chan struct{}
}

type dRequest struct {
	kind   reqKind
	inject Value
}

// NewDirect starts the reference goroutine in Created state.

func NewDirect(g *frontend.GenDecl) *Direct {
	d := &Direct{prog: g, state: Created, ready: make(chan struct{})}
	go d.runner()
	<-d.ready
	return d
}

func (d *Direct) Name() string { return d.prog.Name }
func (d *Direct) State() State { d.mu.Lock(); defer d.mu.Unlock(); return d.state }

func (d *Direct) Snapshot() Snapshot {
	d.mu.Lock()
	defer d.mu.Unlock()
	return Snapshot{Name: d.prog.Name, State: d.state.String(), Locals: map[string]Value{}}
}

func (d *Direct) Next() Outcome { return d.send(dRequest{kind: reqNext}) }
func (d *Direct) Throw(v Value) Outcome {
	return d.send(dRequest{kind: reqThrow, inject: v})
}
func (d *Direct) Close() Outcome { return d.send(dRequest{kind: reqClose}) }

func terminalError(st State, code, msg string) Outcome {
	return Outcome{Kind: OutFailed, State: st, ErrCode: code, ErrMsg: msg}
}

func (d *Direct) send(req dRequest) Outcome {
	d.mu.Lock()
	switch d.state {
	case Running:
		st := d.state
		d.mu.Unlock()
		return terminalError(st, "generator_running", "generator is already running")
	case Closed, Done, Failed:
		st := d.state
		d.mu.Unlock()
		return terminalError(st, "generator_closed", "generator is in terminal state "+st.String())
	case Created:
		if req.kind == reqClose {
			d.state = Closed
			d.mu.Unlock()
			return Outcome{Kind: OutClosedOK, State: Closed}
		}
	}

	if !d.parked || d.current == nil {
		st := d.state
		d.mu.Unlock()
		return terminalError(st, "generator_running", "generator is not parked")
	}
	w := d.current
	d.state = Running
	d.parked = false
	d.current = nil
	d.mu.Unlock()

	w.req = req
	close(w.ack)
	out := <-w.finish

	d.mu.Lock()
	d.state = out.State
	// On suspension the goroutine has already installed a fresh wire inside
	// parkAtYield before delivering the outcome, so d.current is the next one;
	// do not overwrite it with the stale w.
	if out.State != Suspended {
		d.parked = false
		d.current = nil
	}
	d.mu.Unlock()
	return out
}
