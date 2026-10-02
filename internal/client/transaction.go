// Package client is a STUN Binding client over a single UDP socket. Its job is
// transaction bookkeeping: a response may only complete a request when the
// transaction id AND the transport source address match, and each response is
// consumed at most once. A late datagram for a timed-out request is dropped, so
// an old response can never complete a newer request.
package client

import (
	"sync"
	"time"

	"stunlab/internal/stun"
)

// pendingEntry is one outstanding request.
type pendingEntry struct {
	server   string // host:port the request was sent to
	deadline time.Time
	reply    chan *inbound
}

// inbound is a datagram offered to the table by the read loop.
type inbound struct {
	raw    []byte
	source string
}

// TransactionTable is the bounded set of outstanding Binding requests keyed by
// transaction id.
type TransactionTable struct {
	mu      sync.Mutex
	pending map[stun.TransactionID]*pendingEntry
	maxSize int
}

// NewTransactionTable creates a table capped at maxSize outstanding requests.
func NewTransactionTable(maxSize int) *TransactionTable {
	return &TransactionTable{pending: map[stun.TransactionID]*pendingEntry{}, maxSize: maxSize}
}

// Add registers txnID. It returns KindResourceExhausted if the table is full
// and KindStateConflict if the (random) id is already outstanding.
func (t *TransactionTable) Add(txnID stun.TransactionID, server string, timeout time.Duration) (<-chan *inbound, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if _, exists := t.pending[txnID]; exists {
		return nil, &stun.Error{
			Kind:   stun.KindStateConflict,
			Op:     "TransactionTable.Add",
			Detail: "transaction id already outstanding",
		}
	}
	if len(t.pending) >= t.maxSize {
		return nil, &stun.Error{
			Kind:   stun.KindResourceExhausted,
			Op:     "TransactionTable.Add",
			Detail: "pending transaction table full",
		}
	}
	e := &pendingEntry{
		server:   server,
		deadline: time.Now().Add(timeout),
		reply:    make(chan *inbound, 1),
	}
	t.pending[txnID] = e
	return e.reply, nil
}

// Take atomically removes and returns the waiter for txnID. The second result
// is false if no request with that id is outstanding (stale/unknown
// transaction).
func (t *TransactionTable) Take(txnID stun.TransactionID) (*pendingEntry, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	e, ok := t.pending[txnID]
	if ok {
		delete(t.pending, txnID)
	}
	return e, ok
}

// Size reports the number of outstanding requests.
func (t *TransactionTable) Size() int {
	t.mu.Lock()
	defer t.mu.Unlock()
	return len(t.pending)
}

// Deliver matches an inbound datagram against an outstanding request and
// hands it over exactly once. Delivery outcomes let the caller log *why* a
// datagram was accepted or rejected:
//
//   - matched:  entry found and source equals the request target
//   - source_mismatch: entry found but the datagram came from another address
//   - stale:    no outstanding transaction (old or forged response)
func (t *TransactionTable) Deliver(pkt *inbound) string {
	id, ok := peekTxnID(pkt.raw)
	if !ok {
		return "malformed"
	}
	t.mu.Lock()
	e, ok := t.pending[id]
	if !ok {
		t.mu.Unlock()
		return "stale"
	}
	if e.server != pkt.source {
		t.mu.Unlock()
		return "source_mismatch"
	}
	delete(t.pending, id)
	ch := e.reply
	t.mu.Unlock()
	ch <- pkt
	return "matched"
}

// peekTxnID extracts the transaction id without fully parsing: enough to
// demux a datagram. Structural validation happens at request completion.
func peekTxnID(b []byte) (stun.TransactionID, bool) {
	var id stun.TransactionID
	if len(b) < stun.HeaderSize {
		return id, false
	}
	copy(id[:], b[8:20])
	return id, true
}
