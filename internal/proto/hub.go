package proto

import (
	"sync"

	"imapd/internal/store"
)

// Hub serialises mailbox mutations that must be observed in one global
// order and fans the resulting untagged events out to every session
// subscribed to the mailbox. All deliveries happen while holding the hub
// mutex, so the commit order of events is exactly the order in which each
// observer receives them.
type Hub struct {
	mu   sync.Mutex
	subs map[string]map[*Session]struct{}
}

func NewHub() *Hub {
	return &Hub{subs: make(map[string]map[*Session]struct{})}
}

// Subscribe adds s to the mailbox's observer set.
func (h *Hub) Subscribe(mailbox string, s *Session) {
	h.mu.Lock()
	defer h.mu.Unlock()
	set := h.subs[mailbox]
	if set == nil {
		set = make(map[*Session]struct{})
		h.subs[mailbox] = set
	}
	set[s] = struct{}{}
}

// Unsubscribe removes s from one mailbox's observer set.
func (h *Hub) Unsubscribe(mailbox string, s *Session) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.subs[mailbox], s)
}

// UnsubscribeAll removes s from every mailbox (session teardown).
func (h *Hub) UnsubscribeAll(s *Session) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, set := range h.subs {
		delete(set, s)
	}
}

// Expunge runs fn (the actual deletion) and then delivers one
// "* <seq> EXPUNGE" per event to every subscribed session, in event
// order, while still holding the lock. Because the deletion and the
// fan-out are one critical section, no two expunges can interleave and no
// observer can see a reordered stream.
func (h *Hub) Expunge(mailbox string, fn func() ([]store.Event, error)) ([]store.Event, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	events, err := fn()
	if err != nil {
		return nil, err
	}
	for _, ev := range events {
		for sess := range h.subs[mailbox] {
			sess.deliverExpunge(ev)
		}
	}
	return events, nil
}

// deliverExpunge writes one untagged EXPUNGE to the session. Write
// failures are ignored here: a dead connection is cleaned up by that
// session's own read loop, and must not block other observers.
func (s *Session) deliverExpunge(ev store.Event) {
	if s.w != nil {
		s.w.Untaggedf("%d EXPUNGE", ev.Seq)
	}
}
