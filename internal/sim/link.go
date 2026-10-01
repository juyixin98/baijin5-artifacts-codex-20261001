package sim

import (
	"math/rand"
	"time"
)

// LinkProperties is the outcome of the link model for one datagram.
type LinkProperties struct {
	Delay     time.Duration // base one-way propagation delay
	Jitter    time.Duration // signed additive jitter for this copy
	Drop      bool          // datagram is lost
	Duplicate bool          // an extra copy follows
	DupGap    time.Duration // trailing delay of the duplicate
	DupJitter time.Duration // jitter applied to the duplicate
}

// LinkModel decides per-datagram link properties for the directed pair
// (from -> to) at virtual time t. Implementations must be safe for use from
// the single pump goroutine.
type LinkModel interface {
	Props(from, to string, t time.Time) LinkProperties
}

// AsymLink is a direction-aware link model: delay differs for the request
// direction (client->server) and reply direction, which is exactly the
// asymmetry a single NTP exchange cannot remove.
//
// Direction is decided by endpoint-name suffix conventions in scenarios, but
// to stay generic AsymLink holds two maps keyed by directed pair, with a
// fallback default.
type AsymLink struct {
	// Directed delays: "src->dst".
	Delays map[string]time.Duration
	// Jitter applied uniformly in [-Jitter/2, Jitter/2] when non-zero.
	Jitter time.Duration
	// LossRate in [0,1] per directed pair ("src->dst").
	LossRate map[string]float64
	// DupRate in [0,1] per directed pair.
	DupRate map[string]float64
	// DupGap is the trailing delay of duplicates.
	DupGap time.Duration

	// Default delay when a pair is unmapped.
	DefaultDelay time.Duration

	rng *rand.Rand
}

// NewAsymLink seeds the model deterministically.
func NewAsymLink(seed int64) *AsymLink {
	return &AsymLink{
		Delays:       map[string]time.Duration{},
		LossRate:     map[string]float64{},
		DupRate:      map[string]float64{},
		DupGap:       20 * time.Millisecond,
		DefaultDelay: time.Millisecond,
		rng:          rand.New(rand.NewSource(seed)),
	}
}

// SetDelay registers a one-way delay for the directed pair src->dst.
func (a *AsymLink) SetDelay(src, dst string, d time.Duration) *AsymLink {
	a.Delays[key(src, dst)] = d
	return a
}

// SetLoss registers independent loss probability for src->dst.
func (a *AsymLink) SetLoss(src, dst string, rate float64) *AsymLink {
	a.LossRate[key(src, dst)] = rate
	return a
}

// SetDuplication registers independent duplication probability for src->dst.
func (a *AsymLink) SetDuplication(src, dst string, rate float64) *AsymLink {
	a.DupRate[key(src, dst)] = rate
	return a
}

// Props implements LinkModel.
func (a *AsymLink) Props(from, to string, t time.Time) LinkProperties {
	k := key(from, to)
	d, ok := a.Delays[k]
	if !ok {
		d = a.DefaultDelay
	}
	p := LinkProperties{Delay: d}
	if a.Jitter > 0 {
		j := a.rng.Int63n(int64(a.Jitter)) - int64(a.Jitter)/2
		p.Jitter = time.Duration(j)
	}
	if rate := a.LossRate[k]; rate > 0 && a.rng.Float64() < rate {
		p.Drop = true
	}
	if rate := a.DupRate[k]; rate > 0 && a.rng.Float64() < rate {
		p.Duplicate = true
		p.DupGap = a.DupGap
		if a.Jitter > 0 {
			j := a.rng.Int63n(int64(a.Jitter)) - int64(a.Jitter)/2
			p.DupJitter = time.Duration(j)
		}
	}
	return p
}

// StaticLink returns the same properties every time (tests/oracles).
type StaticLink struct {
	P LinkProperties
}

// Props implements LinkModel.
func (s StaticLink) Props(from, to string, t time.Time) LinkProperties { return s.P }

// ScriptedLink lets a scenario vary behavior per call index per directed pair,
// e.g. to inject one huge latency spike or a single loss event.
type ScriptedLink struct {
	// Script[pair] is consumed in order; exhausted entries fall back to Base.
	Script map[string][]LinkProperties
	Base   LinkProperties
	n      map[string]int
}

// Props implements LinkModel.
func (s *ScriptedLink) Props(from, to string, t time.Time) LinkProperties {
	if s.n == nil {
		s.n = map[string]int{}
	}
	k := key(from, to)
	i := s.n[k]
	s.n[k] = i + 1
	if seq, ok := s.Script[k]; ok && i < len(seq) {
		return seq[i]
	}
	return s.Base
}

func key(src, dst string) string { return src + "->" + dst }
