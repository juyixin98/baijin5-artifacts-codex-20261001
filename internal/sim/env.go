// Package sim provides a deterministic, virtual-time datagram fabric used to
// synthesize local NTP samples. A single pump goroutine owns virtual time and
// the event heap; server handlers run INLINE on the pump, so there are no
// scheduling races and every scenario is byte-for-byte reproducible.
//
// Virtual-time correctness protocol: when the pump delivers a datagram or
// fires a timer, it HANDSHAKES with the receiving goroutine (deliver, then
// wait for an acknowledgement before popping any later event). This parks
// virtual time at the exact delivery instant until the observer has read its
// timestamp, so a client can never stamp t4 with a later virtual time merely
// because the goroutine had not been scheduled yet.
//
// The fabric models:
//
//   - per-direction asymmetric link delay (client->server vs server->client)
//   - seeded random loss / duplication / jitter
//   - per-source clock offset, linear drift and arbitrary clock steps
//   - sources that stop answering, spike in latency, or send KoD/LI=3
//
// It never opens a real socket (except via the separate transport/UDP path)
// and never modifies any clock.
package sim

import (
	"container/heap"
	"sync"
	"time"
)

// ---- event heap ---------------------------------------------------------

type eventKind uint8

const (
	evPacket   eventKind = iota // deliver a datagram
	evTimer                     // wake a blocked Sleep
	evDeadline                  // expire a registered receive
)

type event struct {
	at   time.Time
	seq  uint64
	kind eventKind
	idx  int // heap index for eager removal

	// packet fields
	pkt *queuedPacket
	// timer/deadline fields
	wake chan struct{} // signal receiver (timer or receive)
	ack  chan struct{} // receiver parks the pump until observed
}

type queuedPacket struct {
	from string
	to   string
	data []byte
	at   time.Time // virtual delivery instant
}

type eventHeap []*event

func (h eventHeap) Len() int { return len(h) }
func (h eventHeap) Less(i, j int) bool {
	if !h[i].at.Equal(h[j].at) {
		return h[i].at.Before(h[j].at)
	}
	return h[i].seq < h[j].seq
}
func (h eventHeap) Swap(i, j int) {
	h[i], h[j] = h[j], h[i]
	h[i].idx = i
	h[j].idx = j
}
func (h *eventHeap) Push(x any) {
	x.(*event).idx = len(*h)
	*h = append(*h, x.(*event))
}
func (h *eventHeap) Pop() any {
	old := *h
	n := len(old)
	x := old[n-1]
	old[n-1] = nil
	*h = old[:n-1]
	return x
}

// ---- requests into the pump --------------------------------------------

type sendReq struct {
	from, to string
	data     []byte
	reply    chan struct{} // closed once ingested (time stays at "now")
}

type recvReq struct {
	box      string
	deadline time.Time // zero => block forever
	ch       chan recvResult
	ack      chan struct{}
}

type recvResult struct {
	data []byte
	from string
	err  error
}

type sleepReq struct {
	until time.Time
	wake  chan struct{}
	ack   chan struct{}
}

type serveReq struct {
	name    string
	handler PacketHandler
}

type registerReq struct {
	name string
	done chan struct{}
}

// PacketHandler processes one inbound datagram on the pump goroutine and
// returns zero or more outbound datagrams. Inline execution makes server
// behavior deterministic.
type PacketHandler func(in Incoming) []Outgoing

// Incoming is one delivered datagram handed to a handler.
type Incoming struct {
	From string
	Data []byte
	At   time.Time // virtual receive time
}

// Outgoing is one handler reply.
type Outgoing struct {
	To   string
	Data []byte
}

type boxState struct {
	queue            []*queuedPacket
	receiver         *receiver
	receiverDeadline *event
	handler          PacketHandler
}

// receiver is a live blocked Recv call.
type receiver struct {
	ch  chan recvResult
	ack chan struct{}
}

// Environment is the virtual clock, event heap and pump.
type Environment struct {
	mu     sync.Mutex
	curNow time.Time

	reqs   chan any
	wg     sync.WaitGroup
	stopCh chan struct{}
}

// NewEnvironment creates an environment anchored at start with the given link
// model (nil means zero-delay, lossless links).
func NewEnvironment(start time.Time, links LinkModel) *Environment {
	if links == nil {
		links = StaticLink{}
	}
	e := &Environment{
		curNow: start,
		reqs:   make(chan any, 64),
		stopCh: make(chan struct{}),
	}
	e.wg.Add(1)
	go e.pump(start, links)
	return e
}

// Now returns the current virtual time.
func (e *Environment) Now() time.Time {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.curNow
}

// Stop terminates the pump.
func (e *Environment) Stop() {
	close(e.stopCh)
	e.wg.Wait()
}

// Serve registers a server endpoint with an inline packet handler.
func (e *Environment) Serve(name string, h PacketHandler) {
	e.reqs <- serveReq{name: name, handler: h}
}

// Endpoint returns a datagram endpoint bound to name, creating its mailbox
// synchronously BEFORE any packet can be sent.
func (e *Environment) Endpoint(name string) *DatagramEndpoint {
	done := make(chan struct{})
	e.reqs <- registerReq{name: name, done: done}
	<-done
	return &DatagramEndpoint{env: e, name: name}
}

// Sleep blocks the caller until virtual time reaches now+d. The pump is parked
// at the wake instant until the caller observes it.
func (e *Environment) Sleep(d time.Duration) {
	e.mu.Lock()
	until := e.curNow.Add(d)
	e.mu.Unlock()
	wake := make(chan struct{})
	ack := make(chan struct{})
	e.reqs <- sleepReq{until: until, wake: wake, ack: ack}
	<-wake
	close(ack)
}

// pump is the single mutator of virtual time.
func (e *Environment) pump(start time.Time, links LinkModel) {
	defer e.wg.Done()
	now := start
	events := eventHeap{}
	heap.Init(&events)
	boxes := map[string]*boxState{}
	var seq uint64

	pushEvent := func(ev *event) {
		seq++
		ev.seq = seq
		heap.Push(&events, ev)
	}

	// ingest applies the link model to one outbound datagram.
	ingest := func(from, to string, data []byte) {
		props := links.Props(from, to, now)
		makeEvent := func(delay time.Duration) *event {
			return &event{
				at: now.Add(delay),
				pkt: &queuedPacket{
					from: from,
					to:   to,
					data: append([]byte(nil), data...),
					at:   now.Add(delay),
				},
				kind: evPacket,
			}
		}
		switch {
		case props.Drop:
			// swallowed by the link
		case props.Duplicate:
			pushEvent(makeEvent(props.Delay + props.Jitter))
			pushEvent(makeEvent(props.Delay + props.DupGap + props.DupJitter))
		default:
			pushEvent(makeEvent(props.Delay + props.Jitter))
		}
	}

	// deliver hands a result to a blocked receiver and PARKS the pump until
	// that result has been observed, keeping virtual time exact.
	deliver := func(bs *boxState, r *receiver, res recvResult) {
		bs.receiver = nil
		r.ch <- res
		<-r.ack
	}

	for {
		// Apply every request already pending at the CURRENT virtual instant
		// before allowing time to move forward.
		for {
			select {
			case r := <-e.reqs:
				e.handleReq(r, boxes, ingest, pushEvent, &now, deliver)
			default:
				goto drained
			}
		}
	drained:

		if events.Len() == 0 {
			// Nothing scheduled: block in real time until external input.
			select {
			case <-e.stopCh:
				e.setNow(now)
				return
			case r := <-e.reqs:
				e.handleReq(r, boxes, ingest, pushEvent, &now, deliver)
				continue
			}
		}

		ev := heap.Pop(&events).(*event)
		now = ev.at
		e.setNow(now)

		switch ev.kind {
		case evPacket:
			bs, ok := boxes[ev.pkt.to]
			if !ok {
				continue // nobody home
			}
			if bs.handler != nil {
				// Deterministic inline server handling at this virtual instant;
				// replies are ingested at the same instant.
				outs := bs.handler(Incoming{From: ev.pkt.from, Data: ev.pkt.data, At: now})
				for _, o := range outs {
					ingest(ev.pkt.to, o.To, o.Data)
				}
				continue
			}
			if bs.receiver != nil {
				// Eagerly remove the receiver's timeout so it can never be
				// popped later and spuriously advance virtual time.
				if de := bs.receiverDeadline; de != nil {
					heap.Remove(&events, de.idx)
					bs.receiverDeadline = nil
				}
				deliver(bs, bs.receiver, recvResult{data: ev.pkt.data, from: ev.pkt.from})
			} else {
				bs.queue = append(bs.queue, ev.pkt)
			}
		case evDeadline:
			for _, bs := range boxes {
				if bs.receiver != nil && bs.receiverDeadline == ev {
					bs.receiverDeadline = nil
					deliver(bs, bs.receiver, recvResult{err: ErrVirtualTimeout})
					break
				}
			}
		case evTimer:
			ev.wake <- struct{}{}
			<-ev.ack
		}
	}
}

func (e *Environment) setNow(t time.Time) {
	e.mu.Lock()
	e.curNow = t
	e.mu.Unlock()
}

func (e *Environment) handleReq(
	r any,
	boxes map[string]*boxState,
	ingest func(from, to string, data []byte),
	push func(*event),
	now *time.Time,
	deliver func(bs *boxState, r *receiver, res recvResult),
) {
	switch q := r.(type) {
	case registerReq:
		if _, ok := boxes[q.name]; !ok {
			boxes[q.name] = &boxState{}
		}
		close(q.done)
	case serveReq:
		if _, ok := boxes[q.name]; !ok {
			boxes[q.name] = &boxState{}
		}
		boxes[q.name].handler = q.handler
	case sendReq:
		ingest(q.from, q.to, q.data)
		if q.reply != nil {
			close(q.reply)
		}
	case recvReq:
		bs, ok := boxes[q.box]
		if !ok {
			bs = &boxState{}
			boxes[q.box] = bs
		}
		// Deliver the first queued packet that is not later than the deadline;
		// drop any stale leftovers from an earlier expired wait.
		for len(bs.queue) > 0 {
			qp := bs.queue[0]
			bs.queue = bs.queue[1:]
			if q.deadline.IsZero() || !qp.at.After(q.deadline) {
				r := &receiver{ch: q.ch, ack: q.ack}
				deliver(bs, r, recvResult{data: qp.data, from: qp.from})
				return
			}
		}
		rcv := &receiver{ch: q.ch, ack: q.ack}
		if !q.deadline.IsZero() && !q.deadline.After(*now) {
			// Deadline already in the past: fail immediately.
			deliver(bs, rcv, recvResult{err: ErrVirtualTimeout})
			return
		}
		bs.receiver = rcv
		if !q.deadline.IsZero() {
			de := &event{at: q.deadline, kind: evDeadline}
			push(de)
			bs.receiverDeadline = de
		}
	case sleepReq:
		if !q.until.After(*now) {
			q.wake <- struct{}{}
			<-q.ack
			return
		}
		push(&event{at: q.until, kind: evTimer, wake: q.wake, ack: q.ack})
	}
}
