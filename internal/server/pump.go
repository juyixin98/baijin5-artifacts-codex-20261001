package server

import (
	"imaplite/internal/broker"
	"imaplite/internal/imapwire"
)

// startPump runs the observer loop for a selected mailbox: it drains the
// ordered subscription channel and writes EXPUNGE/EXISTS/FLAGS responses.
// Because the broker delivers one total order per mailbox, every observer
// renders that same order. Overflow (a slow observer) is fatal: state can no
// longer be reconstructed, so the connection gets BYE.
func (s *session) startPump(sel *selectedMailbox) {
	stop := make(chan struct{})
	sel.stop = stop
	go func() {
		for {
			select {
			case <-stop:
				return
			case ev, ok := <-sel.sub.Events():
				if !ok {
					return
				}
				s.renderExternal(ev)
			}
			if sel.sub.Overflowed() {
				s.out.WriteBye("event queue overflowed; mailbox state no longer consistent")
				_ = s.conn.Close()
				return
			}
		}
	}()
}

// renderExternal writes one event produced by another connection. Events from
// this connection are never delivered to its own pump (broker skips origin),
// so there is no double rendering.
func (s *session) renderExternal(ev broker.Event) {
	switch ev.Kind {
	case broker.EvExpunge:
		s.out.WriteExpunge(ev.Seq)
		s.log.externalExpunge(ev.Seq, ev.UID)
	case broker.EvExists:
		s.out.WriteExists(ev.Exists)
		s.log.externalExists(ev.Exists)
	case broker.EvFlags:
		s.out.WriteFetch(ev.Seq, []imapwire.FetchItem{{
			Name:  "FLAGS",
			Kind:  imapwire.KindFlagList,
			Flags: ev.Flags,
		}})
	}
	s.out.Flush()
}
