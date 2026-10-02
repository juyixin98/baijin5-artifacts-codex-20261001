package transport

import (
	"context"
	"errors"
	"fmt"
	"math/rand"
	"net"
	"sync"
	"time"

	"coaplab/internal/diag"
	"coaplab/internal/ids"
	"coaplab/internal/wire"
)

// ErrRST is returned when the peer answers a CON with Reset.
var ErrRST = errors.New("transport: peer sent RST")

// ErrTimeout is returned after MAX_RETRANSMIT unanswered retransmissions.
var ErrTimeout = errors.New("transport: exchange timed out")

// Client is a minimal CoAP client over one UDP socket. It keeps the two
// correlation layers strictly separate:
//
//   - ACKs/RSTs are matched by Message ID (message layer);
//   - application responses (piggybacked or separate) are matched by Token
//     (request layer). A response carrying an unknown token is rejected.
type Client struct {
	conn   *net.UDPConn
	mids   *ids.MIDSource
	tokens *ids.TokenSource
	retx   Retransmit
	rec    *diag.Recorder
	rnd    *rand.Rand

	mu       sync.Mutex
	ackWait  map[uint16]chan *wire.Message // MID  -> ACK/RST
	respWait map[string]chan *wire.Message // Token key -> response
	closed   bool
}

// ClientOptions configures a Client.
type ClientOptions struct {
	LocalAddr   string // "" => ephemeral 127.0.0.1 port
	Retransmit  Retransmit
	MIDSource   *ids.MIDSource
	TokenSource *ids.TokenSource
	Recorder    *diag.Recorder
	Rand        *rand.Rand // nil => deterministic factor 1.0
}

// DialClient binds the client socket and starts its demultiplexing loop.
func DialClient(opts ClientOptions) (*Client, error) {
	if err := opts.Retransmit.Validate(); err != nil {
		return nil, err
	}
	addr, err := resolveUDP(opts.LocalAddr)
	if err != nil {
		return nil, err
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return nil, err
	}
	if opts.MIDSource == nil {
		opts.MIDSource = ids.NewMIDSource()
	}
	if opts.TokenSource == nil {
		ts, _ := ids.NewTokenSource(4)
		opts.TokenSource = ts
	}
	c := &Client{
		conn:     conn,
		mids:     opts.MIDSource,
		tokens:   opts.TokenSource,
		retx:     opts.Retransmit,
		rec:      opts.Recorder,
		rnd:      opts.Rand,
		ackWait:  map[uint16]chan *wire.Message{},
		respWait: map[string]chan *wire.Message{},
	}
	go c.readLoop()
	return c, nil
}

func resolveUDP(addr string) (*net.UDPAddr, error) {
	if addr == "" {
		return &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)}, nil
	}
	return net.ResolveUDPAddr("udp", addr)
}

// LocalAddr reports the bound address.
func (c *Client) LocalAddr() *net.UDPAddr { return c.conn.LocalAddr().(*net.UDPAddr) }

// Close stops the client.
func (c *Client) Close() error {
	c.mu.Lock()
	c.closed = true
	c.mu.Unlock()
	return c.conn.Close()
}

// Exchange performs a CON request. It mints ONE Message ID for the whole
// transaction: every retransmission reuses that same MID so the server's
// dedup cache recognises it (RFC 7252 §4.2). The Token is likewise one
// value for the exchange but serves the independent request layer.
func (c *Client) Exchange(ctx context.Context, remote *net.UDPAddr, req *wire.Message) (*wire.Message, error) {
	if req.Token == nil {
		req.Token = []byte(c.tokens.New())
	}
	tokenKey := ids.Token(req.Token).Key()

	// One MID per transaction; retransmissions MUST repeat it.
	mid := uint16(c.mids.Next())
	req.MID = mid
	req.Type = wire.CON

	ackCh := make(chan *wire.Message, 1)
	respCh := make(chan *wire.Message, 1)
	c.mu.Lock()
	c.ackWait[mid] = ackCh
	c.respWait[tokenKey] = respCh
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		delete(c.ackWait, mid)
		delete(c.respWait, tokenKey)
		c.mu.Unlock()
	}()

	deadline := exchangeDeadline(ctx, c.retx)

	for attempt := 0; attempt <= c.retx.MaxRetransmit; attempt++ {
		raw, err := req.Marshal()
		if err != nil {
			return nil, err
		}
		c.log(remote.String(), mid, req.Token, diag.Accept, "",
			"send CON transmission %d/%d code=%s mid=0x%04x %d bytes",
			attempt+1, c.retx.MaxTransmissions(), req.Code, mid, len(raw))
		if _, err := c.conn.WriteToUDP(raw, remote); err != nil {
			return nil, err
		}

		timer := time.NewTimer(c.retx.Timeout(attempt, c.rnd))
		select {
		case ack := <-ackCh:
			timer.Stop()
			if ack.Type == wire.RST {
				return nil, fmt.Errorf("%w: mid=0x%04x", ErrRST, mid)
			}
			if ack.Code != wire.Empty {
				// Piggybacked response: it must carry THIS request's token.
				if ids.Token(ack.Token).Key() != tokenKey {
					c.log(remote.String(), mid, ack.Token, diag.Reject, diag.CatMessageLayer,
						"piggyback response token mismatch")
					return nil, fmt.Errorf("transport: piggyback response token mismatch mid=0x%04x", mid)
				}
				c.log(remote.String(), mid, ack.Token, diag.Accept, "",
					"piggyback %s: mid matched (message layer), token matched (request layer)", ack.Code)
				return ack, nil
			}
			// Empty ACK: separate response is coming, correlated by token.
			c.log(remote.String(), mid, req.Token, diag.Indeterminate, "",
				"empty ACK mid=0x%04x; awaiting separate response by token", mid)
			select {
			case resp := <-respCh:
				c.log(remote.String(), resp.MID, resp.Token, diag.Accept, "",
					"separate response %s matched by token", resp.Code)
				return resp, nil
			case <-time.After(time.Until(deadline)):
				return nil, fmt.Errorf("%w: no separate response within exchange lifetime", ErrTimeout)
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		case <-timer.C:
			c.log(remote.String(), mid, req.Token, diag.Indeterminate, diag.CatTimeout,
				"ACK timeout after %s; retransmit same MID", c.retx.Timeout(attempt, c.rnd))
			continue
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return nil, fmt.Errorf("%w: after %d transmissions (mid=0x%04x)", ErrTimeout, c.retx.MaxTransmissions(), mid)
}

func exchangeDeadline(ctx context.Context, r Retransmit) time.Time {
	// Generous overall ceiling: retransmission window plus processing slack.
	return time.Now().Add(r.RetransmissionWindow() + r.ACKTimeout*4 + 5*time.Second)
}

func (c *Client) readLoop() {
	buf := make([]byte, 65535)
	for {
		n, raddr, err := c.conn.ReadFromUDP(buf)
		if err != nil {
			c.mu.Lock()
			closed := c.closed
			c.mu.Unlock()
			if closed {
				return
			}
			continue
		}
		dgram := append([]byte(nil), buf[:n]...)
		msg, perr := wire.Parse(dgram)
		if perr != nil {
			c.log(raddr.String(), 0, nil, diag.Reject, diag.CatParseError, "%s", perr)
			continue
		}
		c.dispatch(raddr, msg)
	}
}

func (c *Client) dispatch(raddr *net.UDPAddr, msg *wire.Message) {
	// Message layer: ACK/RST go to the MID waiter.
	if msg.Type == wire.ACK || msg.Type == wire.RST {
		c.mu.Lock()
		ch := c.ackWait[msg.MID]
		c.mu.Unlock()
		if ch != nil {
			select {
			case ch <- msg:
			default:
			}
			return
		}
		// Unsolicited ACK/RST: ignore but diagnose.
		c.log(raddr.String(), msg.MID, msg.Token, diag.Ignore, diag.CatMessageLayer,
			"%s with no outstanding MID", msg.Type)
		return
	}
	// Separate response arrives as CON or NON, matched by Token.
	if msg.Code != wire.Empty {
		c.mu.Lock()
		ch := c.respWait[ids.Token(msg.Token).Key()]
		c.mu.Unlock()
		if ch != nil {
			if msg.Type == wire.CON {
				// Acknowledge the separate response at message layer.
				if raw, err := wire.EmptyACK(msg.MID).Marshal(); err == nil {
					_, _ = c.conn.WriteToUDP(raw, raddr)
				}
			}
			select {
			case ch <- msg:
			default:
			}
			return
		}
		c.log(raddr.String(), msg.MID, msg.Token, diag.Ignore, diag.CatMessageLayer,
			"response with no outstanding token")
	}
}

func (c *Client) log(remote string, mid uint16, token []byte, v diag.Verdict, cat diag.Category, format string, args ...any) {
	if c.rec == nil {
		return
	}
	c.rec.Log(remote, mid, true, ids.Token(token).Hex(), v, cat, format, args...)
}
