package server

import (
	"context"
	"sync/atomic"

	"imaplite/internal/broker"
	"imaplite/internal/imapwire"
	"imaplite/internal/store"
)

// selectedMailbox is a connection's open-mailbox view. uidValidity is snap-
// shotted at SELECT: any later rotation of the epoch makes this view stale,
// after which message-touching commands are refused with UIDVALIDITY.
type selectedMailbox struct {
	name        string
	uidValidity uint32
	uidNext     uint32
	exists      int
	recent      int
	firstUnseen uint32
	readOnly    bool

	sub  *broker.Subscription
	stop chan struct{}

	// stale marks a selection whose UIDVALIDITY changed underneath it.
	stale atomic.Bool
}

// snapshot builds the selected view from SELECT-time counters.
func snapshot(info store.MailboxInfo) *selectedMailbox {
	return &selectedMailbox{
		name:        info.Name,
		uidValidity: info.UIDValidity,
		uidNext:     info.UIDNext,
		exists:      info.Exists,
		recent:      0,
		firstUnseen: info.FirstUnseen,
	}
}

// checkEpoch verifies the mailbox still exists under the SELECTed
// UIDVALIDITY. A mismatch is a state conflict carrying UIDVALIDITY, because
// every UID the client cached is now stale.
func (s *session) checkEpoch(ctx context.Context) (*selectedMailbox, error) {
	sel := s.selected
	info, err := s.srv.store.MailboxInfo(ctx, sel.name)
	if err != nil {
		return nil, wireCompute("mailbox state unavailable: %v", err)
	}
	if info.UIDValidity != sel.uidValidity {
		sel.stale.Store(true)
		return nil, imapwire.NewCodedError(imapwire.ClassState, "UIDVALIDITY",
			"UIDVALIDITY changed from %d to %d; cached UIDs are no longer valid",
			sel.uidValidity, info.UIDValidity)
	}
	sel.uidNext = info.UIDNext
	sel.exists = info.Exists
	return sel, nil
}

var _ = broker.QueueLen
