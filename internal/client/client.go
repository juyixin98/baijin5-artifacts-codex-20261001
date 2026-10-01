// Package client implements a STUN Binding client state machine over UDP.
//
// It enforces three properties called out by the task:
//   - transaction timeout: a request with no valid response by the deadline
//     fails with stunerror.KindExhausted;
//   - response source matching: only a datagram from the exact address the
//     request was sent to can complete the transaction;
//   - transaction isolation: responses carry their 96-bit id, and an old or
//     unsolicited response can never complete a newer request (pending map is
//     keyed by transaction id and entries removed on completion).
package client

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"net"
	"sync"
	"time"

	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

// Config configures a Client.
type Config struct {
	// ServerAddr is the UDP address Binding requests are sent to.
	ServerAddr *net.UDPAddr
	// LocalAddr optionally pins the client socket (e.g. "[::1]:0" for IPv6).
	LocalAddr *net.UDPAddr
	// SharedKey, when non-nil, enables outbound MESSAGE-INTEGRITY and inbound
	// verification. The controlled-test server uses this pre-shared key.
	SharedKey []byte
	// Fingerprint adds/verifies a FINGERPRINT attribute (requires SharedKey).
	Fingerprint bool
	// Timeout bounds each RoundTrip. Default 2s.
	Timeout time.Duration
	// MaxOutstanding bounds the pending-transaction table. Default 64.
	MaxOutstanding int
	// OnEvent, if set, receives protocol state-machine events for the audit
	// log. It must never block for long.
	OnEvent func(Event)
}

// EventType enumerates observable client state transitions.
type EventType string

const (
	EvSend              EventType = "request_sent"
	EvRecvMatched       EventType = "response_matched"
	EvRecvBadSource     EventType = "response_source_mismatch"
	EvRecvUnknownTx     EventType = "response_unknown_transaction"
	EvRecvDecodeError   EventType = "response_decode_error"
	EvTimeout           EventType = "transaction_timeout"
	EvTableFull         EventType = "transaction_table_full"
	EvDuplicateResponse EventType = "response_duplicate_for_completed_tx"
)

// Event is one state-machine observation.
type Event struct {
	Type   EventType
	TxID   stun.TransactionID
	Src    net.Addr
	Kind   stunerror.Kind
	Detail string
}

// Result is the outcome of a successful RoundTrip.
type Result struct {
	TxID     stun.TransactionID
	Msg      *stun.Message
	SrcAddr  *net.UDPAddr
	IP       net.IP
	Port     int
	RTT      time.Duration
	Raw      []byte
	Verified bool // MESSAGE-INTEGRITY present and valid
}

// Client is a STUN Binding client. A Client owns one UDP socket and is safe
// for concurrent RoundTrip calls.
type Client struct {
	cfg     Config
	conn    net.PacketConn
	closeMu sync.Mutex
	closed  bool
	wg      sync.WaitGroup

	mu      sync.Mutex
	pending map[stun.TransactionID]*pending
	seq     uint64
}

type pending struct {
	deadline time.Time
	ch       chan delivery
	seq      uint64
}

type delivery struct {
	msg *stun.Message
	src *net.UDPAddr
	raw []byte
	err error
}

// New dials (or binds) the client socket and starts the reader loop.
func New(cfg Config) (*Client, error) {
	if cfg.ServerAddr == nil {
		return nil, stunerror.New(stunerror.KindInput, "client.new", "ServerAddr is required")
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = 2 * time.Second
	}
	if cfg.MaxOutstanding <= 0 {
		cfg.MaxOutstanding = 64
	}
	if cfg.Fingerprint && cfg.SharedKey == nil {
		return nil, stunerror.New(stunerror.KindInput, "client.new", "fingerprint requires a shared integrity key")
	}

	network := "udp4"
	if cfg.ServerAddr.IP.To4() == nil {
		network = "udp6"
	}
	if cfg.LocalAddr != nil {
		if cfg.LocalAddr.IP.To4() == nil {
			network = "udp6"
		}
	}
	conn, err := net.ListenPacket(network, localString(cfg.LocalAddr))
	if err != nil {
		return nil, stunerror.Wrap(stunerror.KindCompute, "client.new", "bind local socket failed", err)
	}
	// ListenPacket with udp4/udp6 gives a *net.UDPConn; connect for fast send.
	c := &Client{cfg: cfg, conn: conn, pending: make(map[stun.TransactionID]*pending)}
	c.wg.Add(1)
	go c.readLoop()
	return c, nil
}

func localString(a *net.UDPAddr) string {
	if a == nil {
		return ":0"
	}
	return a.String()
}

// LocalAddr reports the bound socket address.
func (c *Client) LocalAddr() *net.UDPAddr {
	return c.conn.LocalAddr().(*net.UDPAddr)
}

// RoundTrip sends a Binding Request and waits for the matching response.
func (c *Client) RoundTrip(ctx context.Context) (*Result, error) {
	var txID stun.TransactionID
	if _, err := rand.Read(txID[:]); err != nil {
		return nil, stunerror.Wrap(stunerror.KindCompute, "client.roundtrip", "transaction id generation failed", err)
	}

	req := stun.NewMessage(stun.BindingRequest, txID)
	rawReq, err := stun.Marshal(req, c.cfg.SharedKey, c.cfg.Fingerprint)
	if err != nil {
		return nil, err
	}

	p := &pending{deadline: time.Now().Add(c.cfg.Timeout), ch: make(chan delivery, 2)}
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil, stunerror.New(stunerror.KindState, "client.roundtrip", "client closed")
	}
	if len(c.pending) >= c.cfg.MaxOutstanding {
		c.mu.Unlock()
		c.emit(Event{Type: EvTableFull, TxID: txID, Kind: stunerror.KindExhausted,
			Detail: "outstanding transaction limit reached"})
		return nil, stunerror.New(stunerror.KindExhausted, "client.roundtrip", "transaction table full")
	}
	if _, exists := c.pending[txID]; exists { // astronomically unlikely with random ids
		c.mu.Unlock()
		return nil, stunerror.New(stunerror.KindState, "client.roundtrip", "duplicate transaction id")
	}
	c.seq++
	p.seq = c.seq
	c.pending[txID] = p
	c.mu.Unlock()

	sentAt := time.Now()
	if _, err := c.conn.WriteTo(rawReq, c.cfg.ServerAddr); err != nil {
		c.remove(txID)
		return nil, stunerror.Wrap(stunerror.KindCompute, "client.roundtrip", "send failed", err)
	}
	c.emit(Event{Type: EvSend, TxID: txID, Detail: "sent to " + c.cfg.ServerAddr.String()})

	timer := time.NewTimer(c.cfg.Timeout)
	defer timer.Stop()
	select {
	case d := <-p.ch:
		c.remove(txID)
		if d.err != nil {
			return nil, d.err
		}
		ip, port, hasXOR, xerr := d.msg.XORMappedAddress()
		if xerr != nil {
			return nil, xerr
		}
		if d.msg.Type != stun.BindingResponse {
			code, reason, _ := d.msg.ErrorCode()
			return nil, stunerror.New(stunerror.KindState, "client.roundtrip",
				"server returned Binding error "+itoa(code)+" "+reason)
		}
		res := &Result{
			TxID:     txID,
			Msg:      d.msg,
			SrcAddr:  d.src,
			Raw:      d.raw,
			RTT:      time.Since(sentAt),
			Verified: d.msg.IntegrityOK,
		}
		if hasXOR {
			res.IP, res.Port = ip, port
		}
		c.emit(Event{Type: EvRecvMatched, TxID: txID, Src: d.src,
			Detail: "matched response after " + res.RTT.String()})
		return res, nil
	case <-timer.C:
		c.remove(txID)
		c.emit(Event{Type: EvTimeout, TxID: txID, Kind: stunerror.KindExhausted,
			Detail: "no matching response within " + c.cfg.Timeout.String()})
		return nil, stunerror.New(stunerror.KindExhausted, "client.roundtrip",
			"transaction timed out after "+c.cfg.Timeout.String())
	case <-ctx.Done():
		c.remove(txID)
		return nil, stunerror.Wrap(stunerror.KindState, "client.roundtrip", "context cancelled", ctx.Err())
	}
}

// readLoop is the single packet consumer; it serializes all demultiplexing.
func (c *Client) readLoop() {
	defer c.wg.Done()
	buf := make([]byte, 2048)
	for {
		n, src, err := c.conn.ReadFrom(buf)
		if err != nil {
			c.closeMu.Lock()
			closed := c.closed
			c.closeMu.Unlock()
			if closed {
				return
			}
			c.failAll(stunerror.Wrap(stunerror.KindCompute, "client.read", "socket read failed", err))
			return
		}
		pkt := make([]byte, n)
		copy(pkt, buf[:n])
		c.handlePacket(pkt, src)
	}
}

func (c *Client) handlePacket(pkt []byte, src net.Addr) {
	udpSrc, ok := src.(*net.UDPAddr)
	if !ok {
		udpSrc = &net.UDPAddr{IP: net.IPv4zero, Port: 0}
	}

	// Parse minimally first to learn the txid; decode fully with integrity key.
	if len(pkt) < stun.HeaderLen {
		c.emit(Event{Type: EvRecvDecodeError, Src: src, Kind: stunerror.KindInput,
			Detail: "datagram shorter than STUN header"})
		return
	}
	var txID stun.TransactionID
	copy(txID[:], pkt[8:20])

	if !c.sourceOK(udpSrc) {
		c.emit(Event{Type: EvRecvBadSource, TxID: txID, Src: src, Kind: stunerror.KindState,
			Detail: "response from " + src.String() + " != server " + c.cfg.ServerAddr.String()})
		return
	}

	msg, derr := stun.Decode(pkt, c.cfg.SharedKey)
	if derr != nil {
		kind := stunerror.Of(derr)
		c.emit(Event{Type: EvRecvDecodeError, TxID: txID, Src: src,
			Kind:   kind,
			Detail: derr.Error()})
		// A forged/tampered response is terminal for this transaction; a
		// merely malformed packet leaves the waiter free to accept a later
		// valid response.
		if kind == stunerror.KindIntegrity {
			c.deliver(txID, delivery{err: derr, src: udpSrc, raw: pkt}, false)
		}
		return
	}

	c.deliver(txID, delivery{msg: msg, src: udpSrc, raw: pkt}, true)
}

// sourceOK enforces exact source matching: IP equal and UDP port equal.
func (c *Client) sourceOK(src *net.UDPAddr) bool {
	return src.IP.Equal(c.cfg.ServerAddr.IP) && src.Port == c.cfg.ServerAddr.Port
}

// deliver routes a parsed datagram to its waiter. When consume is true the
// pending entry is deleted on match, so any later duplicate/stale response
// for the same id is classified as unknown-transaction and can never touch a
// different request. On decode errors (consume=false) the entry is retained
// until the waiter observes the error or the timeout fires.
func (c *Client) deliver(txID stun.TransactionID, d delivery, consume bool) {
	c.mu.Lock()
	p, ok := c.pending[txID]
	if !ok {
		c.mu.Unlock()
		if d.msg != nil {
			c.emit(Event{Type: EvRecvUnknownTx, TxID: txID, Src: d.src,
				Kind:   stunerror.KindState,
				Detail: "response has no outstanding request (late or spoofed)"})
		}
		return
	}
	if consume {
		delete(c.pending, txID)
	}
	c.mu.Unlock()
	p.ch <- d
}

func (c *Client) remove(txID stun.TransactionID) {
	c.mu.Lock()
	delete(c.pending, txID)
	c.mu.Unlock()
}

func (c *Client) failAll(err error) {
	c.mu.Lock()
	all := c.pending
	c.pending = make(map[stun.TransactionID]*pending)
	c.mu.Unlock()
	for id, p := range all {
		select {
		case p.ch <- delivery{err: err}:
		default:
		}
		_ = id
	}
}

// Close shuts the socket down and fails outstanding transactions.
func (c *Client) Close() error {
	c.closeMu.Lock()
	if c.closed {
		c.closeMu.Unlock()
		return nil
	}
	c.closed = true
	c.closeMu.Unlock()
	err := c.conn.Close()
	c.wg.Wait()
	return err
}

func (c *Client) emit(e Event) {
	if c.cfg.OnEvent != nil {
		c.cfg.OnEvent(e)
	}
}

func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	neg := v < 0
	if neg {
		v = -v
	}
	var b [20]byte
	i := len(b)
	for v > 0 {
		i--
		b[i] = byte('0' + v%10)
		v /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

// TxIDSequence is a tiny exported helper for tests that need monotonic
// fixture transaction ids with random trailing bytes.
func TxIDSequence(n uint64) stun.TransactionID {
	var id stun.TransactionID
	binary.BigEndian.PutUint64(id[:8], n)
	_, _ = rand.Read(id[8:])
	return id
}
