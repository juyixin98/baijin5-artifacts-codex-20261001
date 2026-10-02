package transport

import (
	"sync"
	"time"

	"coaplab/internal/ids"
)

// DedupCache is the RFC 7252 §4.2/§4.8 server-side message-layer cache.
//
// A Confirmable message is identified by (source endpoint, Message ID):
// the FIRST datagram of a CON retransmission burst is processed once;
// every later identical (endpoint, MID) datagram is answered by replaying
// the stored acknowledgement/response instead of re-executing the request.
// Token plays no role here — deduplication is purely a message-layer job.
//
// A retransmission that arrives WHILE the first copy is still being handled
// waits for the first response to be stored (bounded by a short deadline)
// and then replays it, rather than being dropped.
type DedupCache struct {
	mu  sync.Mutex
	ttl time.Duration
	now func() time.Time
	m   map[ids.EndpointMID]*dedupEntry
}

type dedupEntry struct {
	firstSeen   time.Time
	requestRaw  []byte
	responseRaw []byte
	ackedEmpty  bool
	done        chan struct{} // closed once responseRaw is stored (or failed)
}

// DedupDecision is what the server does with an inbound datagram.
type DedupDecision struct {
	Duplicate    bool
	Entry        *dedupEntry
	Replay       []byte // stored response datagram to retransmit (nil if none yet)
	SendEmptyACK bool   // first response not ready: re-ack the CON
}

// NewDedupCache creates a cache whose entries expire after ttl
// (normally EXCHANGE_LIFETIME).
func NewDedupCache(ttl time.Duration) *DedupCache {
	return &DedupCache{ttl: ttl, now: time.Now, m: map[ids.EndpointMID]*dedupEntry{}}
}

// Observe registers the first sighting or classifies a duplicate. For a
// duplicate it blocks briefly until the first response is available so the
// caller can replay it. requestRaw is retained for diagnostics.
func (d *DedupCache) Observe(remote string, mid ids.MID, requestRaw []byte) DedupDecision {
	d.mu.Lock()
	d.gcLocked()
	k := ids.EndpointMID{Remote: remote, MID: mid}
	e, ok := d.m[k]
	if ok {
		done := e.done
		d.mu.Unlock()
		d.waitFor(done)
		d.mu.Lock()
		dec := DedupDecision{Duplicate: true, Entry: e}
		if e.responseRaw != nil {
			dec.Replay = e.responseRaw
		} else if e.ackedEmpty {
			dec.SendEmptyACK = true
		}
		d.mu.Unlock()
		return dec
	}
	e = &dedupEntry{firstSeen: d.now(), requestRaw: append([]byte(nil), requestRaw...), done: make(chan struct{})}
	d.m[k] = e
	d.mu.Unlock()
	return DedupDecision{Duplicate: false, Entry: e}
}

// waitFor bounds how long a duplicate waits for the first response.
func (d *DedupCache) waitFor(done chan struct{}) {
	max := 500 * time.Millisecond
	if d.ttl < max {
		max = d.ttl
	}
	select {
	case <-done:
	case <-time.After(max):
	}
}

// StoreResponse records the datagram emitted for the first transmission so
// duplicates can be answered identically.
func (d *DedupCache) StoreResponse(remote string, mid ids.MID, responseRaw []byte) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if e, ok := d.m[ids.EndpointMID{Remote: remote, MID: mid}]; ok {
		if e.responseRaw == nil {
			e.responseRaw = append([]byte(nil), responseRaw...)
			close(e.done)
		}
	}
}

// Fail marks an in-flight first transmission as terminated without a stored
// response so waiting duplicates stop blocking.
func (d *DedupCache) Fail(remote string, mid ids.MID) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if e, ok := d.m[ids.EndpointMID{Remote: remote, MID: mid}]; ok && e.responseRaw == nil {
		select {
		case <-e.done:
		default:
			close(e.done)
		}
	}
}

// MarkEmptyACK records the bare ACK sent while a separate response is built.
func (d *DedupCache) MarkEmptyACK(remote string, mid ids.MID) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if e, ok := d.m[ids.EndpointMID{Remote: remote, MID: mid}]; ok {
		e.ackedEmpty = true
	}
}

func (d *DedupCache) gcLocked() {
	now := d.now()
	for k, e := range d.m {
		if now.Sub(e.firstSeen) > d.ttl {
			delete(d.m, k)
		}
	}
}

// Len returns the live entry count (tests/diagnostics).
func (d *DedupCache) Len() int {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.gcLocked()
	return len(d.m)
}

// SetClock overrides the time source (tests).
func (d *DedupCache) SetClock(now func() time.Time) {
	d.mu.Lock()
	d.now = now
	d.mu.Unlock()
}
