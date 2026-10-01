package protocol

import (
	"fmt"
	"time"
)

// RetransmitParams are the CON reliability parameters from RFC 7252
// section 4.8. They are explicit configuration rather than magic numbers.
type RetransmitParams struct {
	// ACKTimeout is the initial timeout before the first retransmission.
	ACKTimeout time.Duration
	// ACKRandomFactor jitters the timeout uniformly in
	// [ACKTimeout, ACKTimeout*ACKRandomFactor); set to 1.0 in deterministic
	// tests to disable jitter.
	ACKRandomFactor float64
	// MaxRetransmit is the number of retransmissions (not counting the
	// original send). RFC 7252 default is 4, giving 5 total attempts.
	MaxRetransmit int
	// ExchangeLifetime bounds how long a mid exchange state is retained;
	// after it, a late response is treated as unmatchable.
	ExchangeLifetime time.Duration
	// NonLifetime bounds NON dedup/cache retention.
	NonLifetime time.Duration
}

// DefaultParams returns RFC 7252 section 4.8 defaults.
func DefaultParams() RetransmitParams {
	return RetransmitParams{
		ACKTimeout:       2 * time.Second,
		ACKRandomFactor:  1.5,
		MaxRetransmit:    4,
		ExchangeLifetime: 247 * time.Second,
		NonLifetime:      145 * time.Second,
	}
}

// DeterministicParams disables jitter for reproducible tests while keeping
// the RFC timeout shape.
func DeterministicParams() RetransmitParams {
	p := DefaultParams()
	p.ACKRandomFactor = 1.0
	p.ACKTimeout = 100 * time.Millisecond
	p.ExchangeLifetime = 5 * time.Second
	p.NonLifetime = 3 * time.Second
	return p
}

// Validate rejects parameter sets that cannot drive the state machine.
func (p RetransmitParams) Validate() error {
	switch {
	case p.ACKTimeout <= 0:
		return fmt.Errorf("protocol: ACKTimeout must be positive, got %v", p.ACKTimeout)
	case p.ACKRandomFactor < 1.0:
		return fmt.Errorf("protocol: ACKRandomFactor must be >= 1.0, got %v",
			p.ACKRandomFactor)
	case p.MaxRetransmit < 0:
		return fmt.Errorf("protocol: MaxRetransmit must be >= 0, got %d",
			p.MaxRetransmit)
	case p.ExchangeLifetime <= 0:
		return fmt.Errorf("protocol: ExchangeLifetime must be positive, got %v",
			p.ExchangeLifetime)
	case p.NonLifetime <= 0:
		return fmt.Errorf("protocol: NonLifetime must be positive, got %v",
			p.NonLifetime)
	default:
		return nil
	}
}

// backoff returns the wait before retransmission number n (0-indexed): the
// initial timeout doubled each round, optionally jittered by j (uniform
// [0,1)).
func (p RetransmitParams) backoff(n int, j float64) time.Duration {
	factor := 1.0
	if p.ACKRandomFactor > 1.0 && j > 0 {
		factor = 1.0 + j*(p.ACKRandomFactor-1.0)
	}
	d := float64(p.ACKTimeout) * float64(uint64(1)<<uint(n)) * factor
	return time.Duration(d)
}
