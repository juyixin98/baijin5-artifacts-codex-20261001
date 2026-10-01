package protocol

import (
	"encoding/hex"
	"errors"
	"math/rand"
	"net"
	"strconv"
	"sync"
	"time"

	"coapblockwise/internal/diag"
	"coapblockwise/internal/wire"
)

// globalRand seeds ACK jitter; crypto randomness is used for tokens.
var globalRand = rand.New(rand.NewSource(time.Now().UnixNano()))

// defaultRand supplies ACK jitter from a process-global source.
func defaultRand() float64 { return globalRand.Float64() }

func itoa(n uint64) string { return strconv.FormatUint(n, 10) }

func (e *Endpoint) readLoop() {
	defer e.wg.Done()
	buf := make([]byte, wire.MaxDatagram+1)
	for {
		n, addr, err := e.conn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-e.closed:
				return
			default:
			}
			if errors.Is(err, net.ErrClosed) {
				return
			}
			e.log.Error(diag.Ref{Peer: safePeer(addr)}, "UDP read failed",
				"err", err.Error())
			continue
		}
		dgram := append([]byte(nil), buf[:n]...)
		go e.handleDatagram(dgram, cloneAddr(addr))
	}
}

func safePeer(a *net.UDPAddr) string {
	if a == nil {
		return ""
	}
	return a.String()
}

func cloneAddr(a *net.UDPAddr) *net.UDPAddr {
	if a == nil {
		return nil
	}
	cp := *a
	return &cp
}

func (e *Endpoint) handleDatagram(dgram []byte, addr *net.UDPAddr) {
	msg, err := wire.Decode(dgram)
	if err != nil {
		e.log.Warn(diag.Ref{Peer: safePeer(addr)},
			"malformed datagram rejected", "decision", "reject",
			"category", KindMalformed.String(), "reason", err.Error(),
			"bytes", len(dgram))
		return
	}
	mid := msg.MessageID
	ref := diag.Ref{
		MessageID: &mid,
		TokenHex:  hex.EncodeToString(msg.Token),
		Peer:      safePeer(addr),
	}

	switch {
	case msg.IsEmpty() && msg.Type == wire.ACK:
		e.matchEmptyACK(msg, ref)
	case msg.Type == wire.ACK:
		e.matchPiggyback(msg, ref)
	case msg.Type == wire.RST:
		e.matchRST(msg, ref)
	case msg.Type == wire.CON && msg.Code.IsRequest():
		e.serveRequest(msg, addr, ref)
	case msg.Type == wire.CON || msg.Type == wire.NON:
		// Separate response: correlate by TOKEN, never by the fresh MID.
		e.matchSeparateResponse(msg, addr, ref)
	default:
		e.log.Warn(ref, "unclassifiable datagram ignored",
			"decision", "reject", "type", msg.Type.String(),
			"code", msg.Code.String())
	}
}

// ---- client-side matching -------------------------------------------------

func (e *Endpoint) matchEmptyACK(m *wire.Message, ref diag.Ref) {
	c := e.lookupByMID(m.MessageID)
	if c == nil {
		e.log.Warn(ref, "empty ACK unmatched by MID",
			"decision", "reject", "category", KindUnmatched.String())
		return
	}
	c.mu.Lock()
	if c.acked {
		c.mu.Unlock()
		e.log.Debug(ref, "duplicate empty ACK, ignored", "decision", "ignore")
		return
	}
	c.acked = true
	c.mu.Unlock()
	e.log.Info(ref, "empty ACK accepted; awaiting token-matched separate response",
		"decision", "accept", "match", "mid")
	c.cancelTimer()
	// The MID exchange is confirmed; bound the remaining wait for the
	// response associated by token.
	c.armTimer(e.clock.After(e.params.ExchangeLifetime, func() {
		e.finish(c, callResult{err: fail(KindExchangeExpired,
			"empty ACK at MID 0x%04x but no token-matched response within %v",
			m.MessageID, e.params.ExchangeLifetime)})
	}))
}

func (e *Endpoint) matchPiggyback(m *wire.Message, ref diag.Ref) {
	c := e.lookupByMID(m.MessageID)
	if c == nil {
		e.log.Warn(ref, "piggyback ACK unmatched by MID",
			"decision", "reject", "category", KindUnmatched.String())
		return
	}
	if hex.EncodeToString(m.Token) != hex.EncodeToString(c.token) {
		e.finish(c, callResult{err: fail(KindMalformed,
			"piggyback response token does not echo request token")})
		return
	}
	e.log.Info(ref, "piggyback response accepted (MID + token matched)",
		"decision", "accept", "code", m.Code.String(), "payload", m.Payload)
	e.finish(c, callResult{msg: m})
}

func (e *Endpoint) matchRST(m *wire.Message, ref diag.Ref) {
	c := e.lookupByMID(m.MessageID)
	if c == nil {
		e.log.Warn(ref, "RST unmatched by MID",
			"decision", "reject", "category", KindUnmatched.String())
		return
	}
	e.log.Warn(ref, "RST matched: exchange aborted",
		"decision", "reject", "category", KindReset.String())
	e.finish(c, callResult{err: fail(KindReset,
		"peer reset exchange at MID 0x%04x", m.MessageID)})
}

func (e *Endpoint) matchSeparateResponse(m *wire.Message, addr *net.UDPAddr,
	ref diag.Ref,
) {
	c := e.lookupByToken(m.Token)
	if c == nil {
		e.log.Warn(ref,
			"separate response unmatched by token: no request association",
			"decision", "reject", "category", KindUnmatched.String())
		return
	}
	if m.Type == wire.CON {
		// ACK the separate response's OWN fresh MID.
		if raw, err := wire.EmptyACK(m.MessageID).Encode(); err == nil {
			e.sendDatagram(raw, addr, ref, "separate-response ACK")
		}
	}
	e.log.Info(ref, "separate response accepted (token matched; MID independent)",
		"decision", "accept", "code", m.Code.String(), "payload", m.Payload)
	e.finish(c, callResult{msg: m})
}

func (e *Endpoint) lookupByMID(mid uint16) *call {
	e.outMu.Lock()
	defer e.outMu.Unlock()
	return e.outgoing[mid]
}

func (e *Endpoint) lookupByToken(token []byte) *call {
	e.outMu.Lock()
	defer e.outMu.Unlock()
	mid, ok := e.tokenToID[hex.EncodeToString(token)]
	if !ok {
		return nil
	}
	return e.outgoing[mid]
}

// ---- server-side deduplication --------------------------------------------

type dedupEntry struct {
	ready   chan struct{} // closed once raw is final
	raw     []byte        // cached, pre-encoded response datagram
	created time.Time
}

func dedupKey(peer string, mid uint16) string {
	return peer + "/" + strconv.FormatUint(uint64(mid), 16)
}

// inflightGuard serializes the first processing of a MID while letting
// concurrent duplicates wait for the cached response.
type inflightGuard struct {
	mu      sync.Mutex
	entries map[string]*dedupEntry
}

func (e *Endpoint) serveRequest(m *wire.Message, addr *net.UDPAddr, ref diag.Ref) {
	key := dedupKey(addr.String(), m.MessageID)

	e.srvMu.Lock()
	if existing, ok := e.seenRequest[key]; ok && !e.expired(existing.created) {
		e.srvMu.Unlock()
		<-existing.ready // wait for the first response to be finalized
		e.log.Info(ref, "duplicate CON MID: replaying cached response (not reprocessed)",
			"decision", "accept-duplicate", "replayed", true)
		e.sendDatagram(existing.raw, addr, ref, "dedup replay")
		return
	}
	entry := &dedupEntry{ready: make(chan struct{}), created: e.clock.Now()}
	e.seenRequest[key] = entry
	e.srvMu.Unlock()

	e.log.Info(ref, "new CON request accepted for processing",
		"decision", "accept-new", "code", m.Code.String(),
		"path", m.Path(), "payload", m.Payload)

	w := &responseWriter{e: e, req: m, addr: addr}
	handler := e.currentHandler()
	if handler == nil {
		w.Respond(wire.CodeInternal, nil, []byte("no handler installed"))
	} else {
		handler.ServeCOAP(w, &Request{Message: m, Peer: addr})
	}
	raw := w.encoded
	if raw == nil {
		raw, _ = wire.EmptyACK(m.MessageID).Encode()
	}
	entry.raw = raw
	close(entry.ready)
	e.sendDatagram(raw, addr, ref, "response")
}

func (e *Endpoint) expired(t time.Time) bool {
	return e.clock.Now().Sub(t) > e.params.ExchangeLifetime
}

func (e *Endpoint) currentHandler() Handler {
	e.srvMu.Lock()
	defer e.srvMu.Unlock()
	return e.handler
}
