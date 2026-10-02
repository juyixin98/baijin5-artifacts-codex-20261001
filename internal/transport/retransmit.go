package transport

import (
	"fmt"
	"math/rand"
	"time"
)

// Retransmission params, RFC 7252 §4.8. All values are explicit in
// configuration (see internal/config); nothing is silently defaulted.
type Retransmit struct {
	ACKTimeout      time.Duration // initial timeout, default 2s
	ACKRandomFactor float64       // uniform multiplier in [1.0, factor], default 1.5
	MaxRetransmit   int           // retransmissions after the first send, default 4
}

// Validate enforces the RFC bounds (§4.8.1): factor MUST NOT be < 1.0.
func (r Retransmit) Validate() error {
	if r.ACKTimeout <= 0 {
		return fmt.Errorf("transport: ACK_TIMEOUT must be > 0")
	}
	if r.ACKRandomFactor < 1.0 {
		return fmt.Errorf("transport: ACK_RANDOM_FACTOR must be >= 1.0")
	}
	if r.MaxRetransmit < 0 {
		return fmt.Errorf("transport: MAX_RETRANSMIT must be >= 0")
	}
	return nil
}

// Timeout is the wait before retransmission number retrans (0-based).
// RFC: timeout = ACK_TIMEOUT * 2^retrans * random(1, ACK_RANDOM_FACTOR).
// randf may be nil; a deterministic factor of 1.0 is then used (tests).
func (r Retransmit) Timeout(retrans int, randf *rand.Rand) time.Duration {
	base := float64(r.ACKTimeout) * float64(uint64(1)<<uint(retrans))
	f := 1.0
	if r.ACKRandomFactor > 1.0 && randf != nil {
		f = 1.0 + randf.Float64()*(r.ACKRandomFactor-1.0)
	}
	return time.Duration(base * f)
}

// MaxTransmissions is MAX_RETRANSMIT + 1 total sends.
func (r Retransmit) MaxTransmissions() int { return r.MaxRetransmit + 1 }

// ExchangeLifetime is the RFC-derived upper bound for one transaction:
// ACK_TIMEOUT * ((2**MAX_RETRANSMIT) - 1) * ACK_RANDOM_FACTOR (+ MAX_LATENCY*2 etc,
// which we leave to the configured explicit value). The returned duration is
// the retransmission-window portion only.
func (r Retransmit) RetransmissionWindow() time.Duration {
	factor := r.ACKRandomFactor
	if factor < 1.0 {
		factor = 1.0
	}
	return time.Duration(float64(r.ACKTimeout) * float64(int64(1)<<uint(r.MaxRetransmit)-1) * factor)
}
