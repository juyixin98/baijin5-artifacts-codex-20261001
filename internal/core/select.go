package core

import (
	"errors"
	"sort"
	"strconv"
	"time"
)

// Selection status values. Selection never returns OK on partial/unknown data.
type SelectionStatus string

const (
	SelectionOK                  SelectionStatus = "OK"
	SelectionNoSamples           SelectionStatus = "NO_SAMPLES"
	SelectionAllStale            SelectionStatus = "ALL_STALE"
	SelectionInsufficientSources SelectionStatus = "INSUFFICIENT_SOURCES"
	SelectionNoIntersection      SelectionStatus = "NO_INTERSECTION"
	SelectionClockJump           SelectionStatus = "CLOCK_JUMP"
)

// Categorized selection failures.
var (
	ErrNoSamples           = errors.New("core: no samples provided")
	ErrAllStale            = errors.New("core: every sample is stale")
	ErrInsufficientSources = errors.New("core: fewer than the required number of sources")
	ErrNoIntersection      = errors.New("core: candidate intervals do not intersect (source disagreement)")
	ErrClockJump           = errors.New("core: clock jump detected on all latest samples")
)

// Err maps a selection status to its categorized sentinel.
func (st SelectionStatus) Err() error {
	switch st {
	case SelectionOK:
		return nil
	case SelectionNoSamples:
		return ErrNoSamples
	case SelectionAllStale:
		return ErrAllStale
	case SelectionInsufficientSources:
		return ErrInsufficientSources
	case SelectionNoIntersection:
		return ErrNoIntersection
	case SelectionClockJump:
		return ErrClockJump
	default:
		return errors.New("core: unknown selection status " + string(st))
	}
}

// Reason a candidate was admitted or rejected.
type CandidateVerdict string

const (
	VerdictAccepted   CandidateVerdict = "ACCEPTED"
	VerdictSampleBad  CandidateVerdict = "SAMPLE_BAD"
	VerdictStale      CandidateVerdict = "STALE"
	VerdictExtremeRTT CandidateVerdict = "EXTREME_RTT"
	VerdictOutlierRTT CandidateVerdict = "OUTLIER_RTT"
	VerdictClockJump  CandidateVerdict = "CLOCK_JUMP"
)

// SelectionPolicy is the explicit, configurable gating policy.
type SelectionPolicy struct {
	// MaxSampleAge rejects samples whose CollectedAt is older than now-age.
	MaxSampleAge time.Duration
	// MaxRTT rejects samples whose round trip exceeds this hard bound
	// (extreme-latency gate). Zero disables the hard bound.
	MaxRTT time.Duration
	// MaxClockJump rejects a source's newest sample when its point offset moved
	// by more than this relative to the source's previous accepted sample.
	MaxClockJump time.Duration
	// RTTOutlierFactor (>=1) rejects a source when its RTT exceeds
	// factor * median RTT of the fresh population. Zero disables.
	RTTOutlierFactor float64
	// MinSources is the minimum number of distinct accepted sources required
	// to declare a synchronized selection.
	MinSources int
}

// DefaultPolicy returns the built-in conservative defaults.
func DefaultPolicy() SelectionPolicy {
	return SelectionPolicy{
		MaxSampleAge:     90 * time.Second,
		MaxRTT:           5 * time.Second,
		MaxClockJump:     500 * time.Millisecond,
		RTTOutlierFactor: 3.0,
		MinSources:       1,
	}
}

// CandidateEvidence records the decision and its basis for one sample.
type CandidateEvidence struct {
	SourceID    string
	CollectedAt time.Time
	Offset      time.Duration
	RTT         time.Duration
	Lower       time.Duration
	Upper       time.Duration
	Stratum     uint8
	Verdict     CandidateVerdict
	Detail      string
}

// SelectionResult is the engine output with full audit trail.
type SelectionResult struct {
	Status     SelectionStatus
	Reason     string
	SelectedAt time.Time
	SourceID   string
	// Offset is the chosen point correction (midpoint of the intersection).
	Offset time.Duration
	// Lower/Upper bound the true offset after intersection of all accepted
	// source uncertainty intervals.
	Lower time.Duration
	Upper time.Duration
	// AcceptedCount is the number of distinct sources participating.
	AcceptedCount int
	Candidates    []CandidateEvidence
}

// Err returns the categorized failure, or nil when Status == OK.
func (r SelectionResult) Err() error { return r.Status.Err() }

// SourceHistory tracks the previous accepted point offset per source for jump
// detection. Callers retain it across rounds. A nil/empty map means first round.
type SourceHistory map[string]time.Duration

// Select runs the full candidate pipeline:
//
//  1. per-sample validity (already in Sample.Status)
//  2. staleness gate
//  3. extreme-latency hard gate
//  4. clock-jump gate vs previous accepted sample of the same source
//  5. statistical RTT-outlier gate (factor-of-median), only for >=3 sources
//  6. Marzullo intersection of uncertainty intervals
//  7. best-source pick (lowest stratum, then shortest RTT) inside the intersection
//
// now is the evaluator's reference time for the staleness gate. History is
// updated in place with accepted samples.
func Select(samples []Sample, now time.Time, p SelectionPolicy, hist SourceHistory) SelectionResult {
	res := SelectionResult{SelectedAt: now, Status: SelectionNoSamples}
	if len(samples) == 0 {
		res.Reason = "no samples supplied"
		return res
	}
	if hist == nil {
		hist = SourceHistory{}
	}

	ev := make([]CandidateEvidence, 0, len(samples))
	freshRTTs := make([]time.Duration, 0, len(samples))

	// Stage 1-4: validity, staleness, extreme RTT, clock jump.
	for _, s := range samples {
		c := CandidateEvidence{
			SourceID:    s.SourceID,
			CollectedAt: s.CollectedAt,
			Offset:      s.Offset,
			RTT:         s.RTT,
			Lower:       s.Lower,
			Upper:       s.Upper,
			Stratum:     s.Stratum,
		}
		switch {
		case !s.OK():
			c.Verdict = VerdictSampleBad
			c.Detail = "sample status " + string(s.Status) + ": " + s.Reason
		case p.MaxSampleAge > 0 && now.Sub(s.CollectedAt) > p.MaxSampleAge:
			c.Verdict = VerdictStale
			c.Detail = "age " + now.Sub(s.CollectedAt).String() +
				" > max " + p.MaxSampleAge.String()
		case p.MaxRTT > 0 && s.RTT > p.MaxRTT:
			c.Verdict = VerdictExtremeRTT
			c.Detail = "rtt " + s.RTT.String() + " > max " + p.MaxRTT.String()
		default:
			if prev, ok := hist[s.SourceID]; ok && p.MaxClockJump > 0 {
				jump := s.Offset - prev
				if jump < 0 {
					jump = -jump
				}
				if jump > p.MaxClockJump {
					c.Verdict = VerdictClockJump
					c.Detail = "offset moved " + jump.String() +
						" from " + prev.String() + " (max " + p.MaxClockJump.String() + ")"
					ev = append(ev, c)
					continue
				}
			}
			c.Verdict = VerdictAccepted
			c.Detail = "offset=" + s.Offset.String() + " rtt=" + s.RTT.String() +
				" interval=[" + s.Lower.String() + "," + s.Upper.String() + "]"
			hist[s.SourceID] = s.Offset
			freshRTTs = append(freshRTTs, s.RTT)
		}
		ev = append(ev, c)
	}

	// Stage 5: factor-of-median RTT outlier rejection (needs >=3 candidates).
	if p.RTTOutlierFactor >= 1 && len(freshRTTs) >= 3 {
		med := medianDuration(freshRTTs)
		threshold := time.Duration(float64(med) * p.RTTOutlierFactor)
		for i := range ev {
			if ev[i].Verdict != VerdictAccepted {
				continue
			}
			if ev[i].RTT > threshold {
				ev[i].Verdict = VerdictOutlierRTT
				ev[i].Detail = "rtt " + ev[i].RTT.String() +
					" > " + formatFactor(p.RTTOutlierFactor) + "x median " + med.String()
				delete(hist, ev[i].SourceID)
			}
		}
	}

	accepted := make([]CandidateEvidence, 0)
	for _, c := range ev {
		if c.Verdict == VerdictAccepted {
			accepted = append(accepted, c)
		}
	}
	res.Candidates = ev

	if len(accepted) == 0 {
		if allVerdict(ev, VerdictStale) {
			res.Status = SelectionAllStale
			res.Reason = "all " + itoa(len(ev)) + " samples are stale"
			return res
		}
		if allVerdict(ev, VerdictClockJump) {
			res.Status = SelectionClockJump
			res.Reason = "all sources exhibited a clock jump"
			return res
		}
		res.Status = SelectionInsufficientSources
		res.Reason = "0 of " + itoa(len(ev)) + " candidates accepted after gating"
		return res
	}

	// Stage 6: Marzullo intersection.
	lo, hi, count, ok := marzulloIntersection(accepted)
	if !ok || count < p.MinSources {
		res.Status = SelectionNoIntersection
		res.Reason = "max agreement " + itoa(count) + " sources, require " +
			itoa(p.MinSources) + "; source disagreement"
		res.AcceptedCount = len(accepted)
		return res
	}

	// Stage 7: only sources whose interval contains the maximal-agreement
	// window actually endorse it; pick lowest stratum then shortest RTT among
	// those participants (a low-stratum source outside the window must not win).
	participants := make([]CandidateEvidence, 0, count)
	for _, c := range accepted {
		if c.Lower <= lo && c.Upper >= hi {
			participants = append(participants, c)
		}
	}
	sort.Slice(participants, func(i, j int) bool {
		if participants[i].Stratum != participants[j].Stratum {
			return participants[i].Stratum < participants[j].Stratum
		}
		if participants[i].RTT != participants[j].RTT {
			return participants[i].RTT < participants[j].RTT
		}
		return participants[i].SourceID < participants[j].SourceID
	})
	best := participants[0]

	res.Status = SelectionOK
	res.SourceID = best.SourceID
	res.Lower = lo
	res.Upper = hi
	res.Offset = (lo + hi) / 2
	res.AcceptedCount = count
	res.Reason = "intersection of " + itoa(count) + " sources; selected " + best.SourceID +
		" (stratum " + itoa(int(best.Stratum)) + ", rtt " + best.RTT.String() + ")"
	return res
}

// intervalEndpoint is a sweep-line event for Marzullo's algorithm.
type intervalEndpoint struct {
	value time.Duration
	delta int // +1 lower bound (include), -1 upper bound (exclude)
}

// marzulloIntersection returns the tightest interval endorsed by the largest
// number of candidates, that count, and success. It is the classic
// Marzullo/Alvestrand sweep: each interval [lo,hi] emits +1 at lo and -1 at hi;
// the maximal-agreement segment is the answer.
func marzulloIntersection(cs []CandidateEvidence) (lo, hi time.Duration, count int, ok bool) {
	ev := make([]intervalEndpoint, 0, 2*len(cs))
	for _, c := range cs {
		ev = append(ev, intervalEndpoint{c.Lower, +1}, intervalEndpoint{c.Upper, -1})
	}
	// Lower (+1) sorts before upper (-1) at equal values so intervals that
	// merely touch at an endpoint still count as agreeing there.
	sort.Slice(ev, func(i, j int) bool {
		if ev[i].value != ev[j].value {
			return ev[i].value < ev[j].value
		}
		return ev[i].delta > ev[j].delta
	})

	best := 0
	cur := 0
	bestLo := time.Duration(0)
	bestHi := time.Duration(0)
	segStart := time.Duration(0)
	inBest := false
	for i := 0; i < len(ev); i++ {
		e := ev[i]
		if e.delta > 0 {
			cur++
			if cur > best {
				best = cur
				segStart = e.value
				inBest = true
			}
		} else {
			if inBest && cur == best {
				// This upper endpoint closes the maximal-agreement segment.
				bestLo = segStart
				bestHi = e.value
				inBest = false
			}
			cur--
		}
	}
	if best < 1 {
		return 0, 0, 0, false
	}
	return bestLo, bestHi, best, true
}

func formatFactor(f float64) string {
	return strconv.FormatFloat(f, 'f', -1, 64)
}

func medianDuration(xs []time.Duration) time.Duration {
	cp := append([]time.Duration(nil), xs...)
	sort.Slice(cp, func(i, j int) bool { return cp[i] < cp[j] })
	n := len(cp)
	if n%2 == 1 {
		return cp[n/2]
	}
	return (cp[n/2-1] + cp[n/2]) / 2
}

func allVerdict(ev []CandidateEvidence, v CandidateVerdict) bool {
	for _, c := range ev {
		if c.Verdict != v {
			return false
		}
	}
	return len(ev) > 0
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
