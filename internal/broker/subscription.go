package broker

import "sync/atomic"

// Subscription is one connection's ordered event stream for one mailbox.
type Subscription struct {
	id       int64
	topic    *topic
	events   chan Event
	overflow atomic.Bool
}

// Events returns the ordered event channel. It is closed on Unsubscribe.
func (s *Subscription) Events() <-chan Event { return s.events }

// Overflowed reports whether the observer fell behind and events were
// dropped. Callers must treat this as fatal to the selected state (BYE),
// because the connection can no longer reconstruct mailbox state.
func (s *Subscription) Overflowed() bool { return s.overflow.Load() }

// Unsubscribe removes the observer.
func (s *Subscription) Unsubscribe() { s.topic.unsubscribe(s.id) }
