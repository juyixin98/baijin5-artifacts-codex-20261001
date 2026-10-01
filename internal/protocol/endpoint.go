// Package protocol implements the CoAP messaging layer (RFC 7252 section 4):
// CON retransmission with explicit timeouts, server-side Message-ID
// deduplication, and request/response correlation by Token.
//
// The two identifiers are deliberately kept in separate namespaces:
//
//   - Message ID deduplicates a single message exchange (CON/ACK). A
//     retransmitted request carries the SAME MID and must be processed once.
//   - Token correlates a request with its response. A separate response is
//     matched by token even though it has a different MID.
//
// This package knows nothing about block-wise transfers; that state machine
// lives in package blockwise and layers on top.
package protocol

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net"
	"sync"
	"sync/atomic"

	"coapblockwise/internal/diag"
	"coapblockwise/internal/wire"
)

// UDPConn is the subset of *net.UDPConn the Endpoint needs, abstracted so a
// fault-injecting fake can stand in during tests.
type UDPConn interface {
	ReadFromUDP(b []byte) (int, *net.UDPAddr, error)
	WriteToUDP(b []byte, addr *net.UDPAddr) (int, error)
	Close() error
	LocalAddr() net.Addr
}

// Handler processes one request exactly once per unique (peer, MID).
type Handler interface {
	ServeCOAP(w Responder, r *Request)
}

// HandlerFunc adapts a function to Handler.
type HandlerFunc func(w Responder, r *Request)

// ServeCOAP implements Handler.
func (f HandlerFunc) ServeCOAP(w Responder, r *Request) { f(w, r) }

// Request is a decoded inbound request.
type Request struct {
	*wire.Message
	Peer *net.UDPAddr
	// Duplicate reports this was a retransmitted MID served from cache; the
	// handler is never invoked for duplicates, so it is always false here and
	// exists for diagnostics consumers.
	Duplicate bool
}

// Responder lets a handler emit exactly one piggybacked response.
type Responder interface {
	Respond(code wire.Code, opts wire.Options, payload []byte)
}

// Endpoint is one CoAP messaging endpoint bound to a UDP socket.
type Endpoint struct {
	conn   UDPConn
	params RetransmitParams
	clock  Clock
	log    diag.Logger
	rand   func() float64

	outMu     sync.Mutex
	nextMID   uint32
	outgoing  map[uint16]*call
	tokenToID map[string]uint16

	srvMu       sync.Mutex
	handler     Handler
	seenRequest map[string]*dedupEntry

	tokenMu  sync.Mutex
	usedTok  map[string]struct{}

	closeOnce sync.Once
	closed    chan struct{}
	wg        sync.WaitGroup

	exCounter atomic.Uint64
}

type call struct {
	mid       uint16
	token     []byte
	addr      *net.UDPAddr
	done      chan callResult
	timerStop func()
	mu        sync.Mutex
	acked     bool
	finished  bool
	attempts  int
}

type callResult struct {
	msg *wire.Message
	err *Error
}

// Config configures a new Endpoint.
type Config struct {
	Conn    UDPConn
	Params  RetransmitParams
	Clock   Clock
	Logger  diag.Logger
	Rand    func() float64 // 0..1 jitter source; nil uses crypto-seeded math
}

// NewEndpoint constructs an endpoint and starts its receive loop.
func NewEndpoint(cfg Config) (*Endpoint, error) {
	params := cfg.Params
	if params == (RetransmitParams{}) {
		params = DefaultParams()
	}
	if err := params.Validate(); err != nil {
		return nil, err
	}
	clock := cfg.Clock
	if clock == nil {
		clock = NewRealClock()
	}
	logger := cfg.Logger
	if logger == nil {
		logger = diag.Discard()
	}
	e := &Endpoint{
		conn:        cfg.Conn,
		params:      params,
		clock:       clock,
		log:         logger,
		rand:        cfg.Rand,
		outgoing:    make(map[uint16]*call),
		tokenToID:   make(map[string]uint16),
		seenRequest: make(map[string]*dedupEntry),
		usedTok:     make(map[string]struct{}),
		closed:      make(chan struct{}),
	}
	if e.rand == nil {
		e.rand = defaultRand
	}
	e.wg.Add(1)
	go e.readLoop()
	return e, nil
}

// SetHandler installs the server-side request handler.
func (e *Endpoint) SetHandler(h Handler) {
	e.srvMu.Lock()
	defer e.srvMu.Unlock()
	e.handler = h
}

// Close stops the receive loop and releases timers.
func (e *Endpoint) Close() error {
	var err error
	e.closeOnce.Do(func() {
		close(e.closed)
		err = e.conn.Close()
		e.wg.Wait()
	})
	return err
}

// Exchange sends a CON request and blocks until a response, a fatal error
// (RST/malformed), timeout after MaxRetransmit, or ctx cancellation.
// The caller supplies the token; this method allocates the MID. This is the
// concrete enforcement that MID and token are independent values.
//
// Failures come back as a plain error wrapping *protocol.Error; callers use
// AsError to read the machine-readable FailureKind.
func (e *Endpoint) Exchange(ctx context.Context, addr *net.UDPAddr,
	token []byte, code wire.Code, opts wire.Options, payload []byte,
) (*wire.Message, error) {
	if len(token) == 0 || len(token) > wire.MaxTokenLength {
		return nil, fail(KindMalformed,
			"token length must be 1..%d bytes, got %d",
			wire.MaxTokenLength, len(token))
	}
	mid := e.allocMID()
	msg := wire.NewMessage(wire.CON, code, mid, token)
	msg.Options = opts
	msg.Payload = append([]byte(nil), payload...)
	raw, err := msg.Encode()
	if err != nil {
		return nil, fail(KindMalformed, "encode request: %v", err)
	}

	c := &call{
		mid:      mid,
		token:    append([]byte(nil), token...),
		addr:     addr,
		done:     make(chan callResult, 1),
		attempts: 1,
	}
	ref := e.ref(mid, token)

	if !e.registerCall(c) {
		return nil, fail(KindMalformed, "MID/token collision in allocator")
	}
	e.log.Info(ref, "CON exchange start",
		"code", code.String(), "attempts", 1,
		"max_retransmit", e.params.MaxRetransmit,
		"ack_timeout_ms", e.params.ACKTimeout.Milliseconds(),
		"payload", payload)

	// Arm the retransmission timer before the first transmission so a clock
	// can never advance past the deadline before the timer is registered.
	e.armRetransmit(c, raw, 0)
	e.sendDatagram(raw, addr, ref, "request")

	select {
	case res := <-c.done:
		if res.err == nil {
			return res.msg, nil
		}
		return res.msg, res.err
	case <-ctx.Done():
		e.unregisterCall(c)
		return nil, fail(KindCanceled, "exchange canceled: %v", ctx.Err())
	case <-e.closed:
		return nil, fail(KindCanceled, "endpoint closed")
	}
}

// nilIfNil erases the concrete type of a nil *Error so callers see a true nil
// error interface instead of a non-nil pointer-to-nil.
func nilIfNil(e *Error) *Error {
	if e == nil {
		return nil
	}
	return e
}

func (e *Endpoint) allocMID() uint16 {
	e.outMu.Lock()
	defer e.outMu.Unlock()
	mid := uint16(e.nextMID)
	e.nextMID++
	return mid
}

// NewToken returns a random token that is not in use by a live exchange.
func (e *Endpoint) NewToken() ([]byte, error) {
	e.tokenMu.Lock()
	defer e.tokenMu.Unlock()
	for range 16 {
		var b [6]byte
		if _, err := rand.Read(b[:]); err != nil {
			return nil, fail(KindUnknown, "read token bytes: %v", err)
		}
		key := hex.EncodeToString(b[:])
		if _, taken := e.usedTok[key]; !taken {
			e.usedTok[key] = struct{}{}
			return b[:], nil
		}
	}
	return nil, fail(KindUnknown, "could not allocate a unique token in 16 tries")
}

// ReleaseToken returns a token to the pool after the exchange completes.
func (e *Endpoint) ReleaseToken(tok []byte) {
	e.tokenMu.Lock()
	defer e.tokenMu.Unlock()
	delete(e.usedTok, hex.EncodeToString(tok))
}

func (e *Endpoint) registerCall(c *call) bool {
	e.outMu.Lock()
	defer e.outMu.Unlock()
	tkey := hex.EncodeToString(c.token)
	if _, exists := e.tokenToID[tkey]; exists {
		return false
	}
	if _, exists := e.outgoing[c.mid]; exists {
		return false
	}
	e.outgoing[c.mid] = c
	e.tokenToID[tkey] = c.mid
	return true
}

func (e *Endpoint) unregisterCall(c *call) {
	e.outMu.Lock()
	if cur := e.outgoing[c.mid]; cur == c {
		delete(e.outgoing, c.mid)
	}
	tkey := hex.EncodeToString(c.token)
	if mid, ok := e.tokenToID[tkey]; ok && mid == c.mid {
		delete(e.tokenToID, tkey)
	}
	e.outMu.Unlock()
	c.cancelTimer()
	e.ReleaseToken(c.token)
}

func (e *Endpoint) armRetransmit(c *call, raw []byte, n int) {
	wait := e.params.backoff(n, e.rand())
	ref := e.ref(c.mid, c.token)
	stop := e.clock.After(wait, func() {
		c.mu.Lock()
		acked := c.acked
		attempt := c.attempts
		c.mu.Unlock()
		if acked {
			return // separate-response wait is armed elsewhere
		}
		if attempt > e.params.MaxRetransmit {
			e.log.Error(ref, "CON exchange failed: retransmissions exhausted",
				"attempts", attempt,
				"max_retransmit", e.params.MaxRetransmit)
			e.finish(c, callResult{
				err: fail(KindTimeout,
					"no ACK after %d transmissions (initial + %d retransmit)",
					attempt, e.params.MaxRetransmit),
			})
			return
		}
		c.mu.Lock()
		c.attempts++
		c.mu.Unlock()
		e.log.Warn(ref, "ACK timeout, retransmitting CON",
			"attempts", c.attempts, "waited_ms", wait.Milliseconds())
		e.sendDatagram(raw, c.addr, ref, "retransmit")
		e.armRetransmit(c, raw, n+1)
	})
	c.mu.Lock()
	c.timerStop = stop
	c.mu.Unlock()
}

// cancelTimer stops whichever timer is currently armed on the call, if any.
func (c *call) cancelTimer() {
	c.mu.Lock()
	stop := c.timerStop
	c.timerStop = nil
	c.mu.Unlock()
	if stop != nil {
		stop()
	}
}

// armTimer replaces the armed timer.
func (c *call) armTimer(stop func()) {
	c.mu.Lock()
	c.timerStop = stop
	c.mu.Unlock()
}

func (e *Endpoint) finish(c *call, res callResult) {
	c.mu.Lock()
	if c.finished {
		c.mu.Unlock()
		return
	}
	c.finished = true
	c.mu.Unlock()
	c.done <- res
	e.unregisterCall(c)
}

func (e *Endpoint) ref(mid uint16, token []byte) diag.Ref {
	id := e.exCounter.Add(1)
	return diag.Ref{
		Exchange:  "ex-" + itoa(id),
		MessageID: &mid,
		TokenHex:  hex.EncodeToString(token),
	}
}
