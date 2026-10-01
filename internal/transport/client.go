package transport

import (
	"errors"
	"fmt"
	"time"

	"ntpsim/internal/clock"
	"ntpsim/internal/core"
	"ntpsim/internal/protocol"
)

// Client-side failure categories, kept distinct from core sample categories
// (a packet that never arrived or failed validation must not look like math).
var (
	// ErrOriginMismatch: reply did not echo the request's transmit timestamp.
	// Per RFC 5905 the reply must be discarded (replay, forgery, or late packet
	// from an earlier exchange).
	ErrOriginMismatch = errors.New("transport: origin timestamp mismatch")
	// ErrMalformedReply: reply failed protocol decoding.
	ErrMalformedReply = errors.New("transport: malformed reply")
	// ErrWrongPeer: reply came from a different address than requested.
	ErrWrongPeer = errors.New("transport: reply from unexpected peer")
	// ErrOnlyStaleReplies: the deadline expired after discarding one or more
	// replayed/late packets; no fresh reply was ever received.
	ErrOnlyStaleReplies = errors.New("transport: deadline expired; only stale/replayed replies received")
)

// ExchangeConfig controls one client request/response round.
type ExchangeConfig struct {
	// Timeout bounds the wait for a reply.
	Timeout time.Duration
	// Epsilon widens the uncertainty interval with local precision noise.
	Epsilon time.Duration
	// RequirePeer, when non-empty, demands Recv's "from" equal this address.
	RequirePeer string
}

// DefaultExchangeConfig is a 1 s timeout with 50 us local precision epsilon.
func DefaultExchangeConfig() ExchangeConfig {
	return ExchangeConfig{Timeout: time.Second, Epsilon: 50 * time.Microsecond}
}

// Client drives the request side of the protocol state machine over a Clock.
type Client struct {
	clk clock.Clock
}

// NewClient builds a client reading t1/t4 from clk. The clock is never set.
func NewClient(clk clock.Clock) *Client { return &Client{clk: clk} }

// Exchange performs one NTP round against peer via dg and returns the
// evaluated core.Sample. Transport/protocol failures are returned as
// categorized errors; packet-level semantic failures (negative delay, KoD,
// unsynchronized server) appear inside the Sample, not as Go errors.
//
// Replies that do not echo the current request's origin timestamp are
// discarded as stale/replayed (RFC 5905 §8); the client keeps waiting until
// the deadline, then fails with ErrOnlyStaleReplies rather than accepting one.
func (c *Client) Exchange(dg Datagram, peer string, cfg ExchangeConfig) (core.Sample, error) {
	t1 := c.clk.Now()
	req := protocol.NewClientRequest(t1)
	if err := dg.Send(req.Encode(), peer); err != nil {
		return core.Sample{}, fmt.Errorf("transport: send failed: %w", err)
	}

	deadline := t1.Add(cfg.Timeout)
	staleDiscards := 0
	for {
		b, from, err := dg.Recv(deadline)
		t4 := c.clk.Now()
		if err != nil {
			if errors.Is(err, ErrTimeout) {
				if staleDiscards > 0 {
					return core.Sample{}, fmt.Errorf(
						"transport: no fresh reply from %s within %s (%d stale packet(s) discarded): %w",
						peer, cfg.Timeout, staleDiscards, ErrOnlyStaleReplies)
				}
				return core.Sample{}, fmt.Errorf("transport: no reply from %s within %s: %w",
					peer, cfg.Timeout, ErrTimeout)
			}
			return core.Sample{}, fmt.Errorf("transport: recv failed: %w", err)
		}
		if cfg.RequirePeer != "" && from != cfg.RequirePeer {
			return core.Sample{}, fmt.Errorf("%w: got %s want %s", ErrWrongPeer, from, cfg.RequirePeer)
		}

		p, derr := protocol.Decode(b)
		if derr != nil {
			return core.Sample{}, fmt.Errorf("%w: %v", ErrMalformedReply, derr)
		}

		// Anti-replay: the server MUST echo our exact transmit timestamp.
		if p.OriginTime != req.TransmitTime {
			staleDiscards++
			continue
		}

		raw := core.RawTimestamps{
			SourceID:       peer,
			T1:             t1,
			T2:             p.ReceiveTime.Time(),
			T3:             p.TransmitTime.Time(),
			T4:             t4,
			CollectedAt:    t4,
			Stratum:        p.Stratum,
			LI:             p.LI,
			RootDelay:      p.RootDelay.Duration(),
			RootDispersion: p.RootDispersion.Duration(),
		}
		if p.IsKissOfDeath() {
			raw.KissCode = p.KissCode()
		}
		return core.Evaluate(raw, cfg.Epsilon), nil
	}
}
