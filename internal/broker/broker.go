// Package broker is the in-process, per-mailbox event bus. It gives every
// observer of the same mailbox one total order of state changes: mutating
// operations are serialised by a mailbox mutex and their events flow through
// a single fan-out goroutine, so concurrent EXPUNGE/STORE commands can never
// interleave into two different histories for different connections.
package broker

import (
	"sync"
)

// Kind enumerates the broadcast state changes.
type Kind int

const (
	EvExpunge Kind = iota
	EvExists
	EvFlags
)

// Event is one ordered mailbox state change.
type Event struct {
	Kind   Kind
	Seq    int
	UID    uint32
	Exists int
	Flags  []string
	// Origin is the acting connection's subscription; that connection
	// renders its own events directly, so fan-out skips it. Nil means
	// deliver to every observer (admin/test paths).
	Origin *Subscription
}

// QueueLen is the per-observer buffer. A slower observer than this is
// disconnected with a resource-exhausted BYE rather than allowed to block or
// reorder everyone else's history.
const QueueLen = 1024

// Broker hands out per-mailbox topics, creating one on first use.
type Broker struct {
	mu     sync.Mutex
	topics map[string]*topic
}

// New returns an empty broker.
func New() *Broker { return &Broker{topics: map[string]*topic{}} }

func (b *Broker) topic(name string) *topic {
	b.mu.Lock()
	defer b.mu.Unlock()
	t, ok := b.topics[name]
	if !ok {
		t = newTopic()
		b.topics[name] = t
	}
	return t
}

// Subscribe opens an observer queue for mailbox name.
func (b *Broker) Subscribe(name string) *Subscription {
	return b.topic(name).subscribe()
}

// Mutate serialises a state-changing operation on a mailbox. op performs the
// store mutation and returns the events to publish; origin (the acting
// connection's subscription, or nil for admin/test paths) is stamped on them
// so the fan-out skips that connection, which renders its own events directly.
// Enqueueing happens in order while the mailbox lock is held, fixing one
// total order for every observer.
func (b *Broker) Mutate(name string, origin *Subscription, op func() ([]Event, error)) ([]Event, error) {
	t := b.topic(name)
	t.opMu.Lock()
	defer t.opMu.Unlock()
	events, err := op()
	if err != nil {
		return nil, err
	}
	for i := range events {
		events[i].Origin = origin
		t.publish(events[i])
	}
	return events, nil
}

type topic struct {
	opMu sync.Mutex
	mu   sync.Mutex
	next int64
	subs map[int64]*Subscription
	ch   chan Event
}

func newTopic() *topic {
	t := &topic{
		subs: map[int64]*Subscription{},
		ch:   make(chan Event, 4096),
	}
	go t.fanout()
	return t
}

func (t *topic) fanout() {
	for ev := range t.ch {
		t.mu.Lock()
		for _, sub := range t.subs {
			// The acting connection renders its own events; skip it here.
			if ev.Origin == sub {
				continue
			}
			// Non-blocking delivery: a stalled observer must not reorder
			// the history for everyone else. Mark it overflowed instead.
			select {
			case sub.events <- ev:
			default:
				sub.overflow.Store(true)
			}
		}
		t.mu.Unlock()
	}
}

func (t *topic) publish(ev Event) { t.ch <- ev }

func (t *topic) subscribe() *Subscription {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.next++
	s := &Subscription{
		id:     t.next,
		topic:  t,
		events: make(chan Event, QueueLen),
	}
	t.subs[s.id] = s
	return s
}

func (t *topic) unsubscribe(id int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	sub, ok := t.subs[id]
	if !ok {
		return
	}
	delete(t.subs, id)
	close(sub.events)
}
