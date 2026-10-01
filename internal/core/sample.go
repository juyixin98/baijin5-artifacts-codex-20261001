// Package core contains the on-wire NTP sample math and the candidate clock
// selection engine. It has no I/O dependencies and is fully deterministic,
// which makes it directly testable against hand-computed values.
package core

import (
	"errors"
	"time"
)

// Sample status categories. These are the ONLY valid states; unknown or
// exceptional conditions never collapse into StatusOK.
type Status string

const (
	// StatusOK: delay is non-negative and the sample is mathematically valid.
	StatusOK Status = "OK"
	// StatusNegativeDelay: round-trip delay is negative. With monotonic clocks
	// this is impossible, so the sample is invalid (replayed/forged reply,
	// clock step, or corrupted timestamps).
	StatusNegativeDelay Status = "NEGATIVE_DELAY"
	// StatusZeroTimestamp: the server left Receive/Transmit at zero (or the
	// client never got a valid stamp).
	StatusZeroTimestamp Status = "ZERO_TIMESTAMP"
	// StatusKissOfDeath: server replied with stratum 0 and a kiss code.
	StatusKissOfDeath Status = "KISS_OF_DEATH"
	// StatusUnsynchronized: server LI field is 3 (alarm), clock not synchronized.
	StatusUnsynchronized Status = "UNSYNCHRONIZED"
)

// Categorized failure sentinels. Callers use errors.Is to branch on failure class.
var (
	// ErrNegativeDelay is reported when (t4-t1)-(t3-t2) < 0.
	ErrNegativeDelay = errors.New("core: negative round-trip delay")
	// ErrZeroTimestamp is reported when a required timestamp is the zero value.
	ErrZeroTimestamp = errors.New("core: missing zero timestamp")
	// ErrKissOfDeath is reported for stratum-0 kiss-o'-death replies.
	ErrKissOfDeath = errors.New("core: kiss-o'-death reply")
	// ErrUnsynchronized is reported when the server LI field is 3.
	ErrUnsynchronized = errors.New("core: server clock unsynchronized (LI=3)")
)

// RawTimestamps are the four on-wire timestamps plus response metadata.
//
//	t1 (Origin):     client send time,    client clock
//	t2 (Receive):    server receive time, server clock
//	t3 (Transmit):   server send time,    server clock
//	t4 (Destination): client receive time, client clock
//
// All four are stored as time.Time values; units at the boundary are handled
// by the protocol layer.
type RawTimestamps struct {
	SourceID       string
	T1             time.Time
	T2             time.Time
	T3             time.Time
	T4             time.Time
	CollectedAt    time.Time
	Stratum        uint8
	LI             uint8
	KissCode       string
	RootDelay      time.Duration
	RootDispersion time.Duration
}

// Sample is a fully evaluated four-timestamp exchange.
type Sample struct {
	SourceID string

	// Raw legs, signed as observed (a leg can be negative when the true clock
	// offset exceeds the true one-way delay; that is NOT an error).
	Forward  time.Duration // f = t2 - t1
	Backward time.Duration // b = t4 - t3

	// Offset is the NTP on-wire offset estimate: theta = (f - b) / 2.
	// Positive means the server clock is AHEAD of the client.
	Offset time.Duration
	// RTT is the total round-trip: delta = (t4 - t1) - (t3 - t2) = f + b.
	RTT time.Duration

	// TrueOffset[Lower, Upper] bounds the TRUE clock offset. The asymmetry of
	// the forward/backward path cannot be removed from a single exchange; with
	// true one-way delays constrained to be >= 0 the true offset lies within
	// an RTT-wide interval centered on the estimate.
	//
	//	Lower = theta - delta/2 - epsilon
	//	Upper = theta + delta/2 + epsilon
	Lower time.Duration
	Upper time.Duration

	// Epsilon is the extra uncertainty from server root dispersion and client
	// precision used to widen the interval.
	Epsilon        time.Duration
	RootDelay      time.Duration
	RootDispersion time.Duration
	Stratum        uint8
	LI             uint8
	KissCode       string
	CollectedAt    time.Time
	Status         Status
	Reason         string
}

// OK reports whether the sample passed all validity checks.
func (s Sample) OK() bool { return s.Status == StatusOK }

// Err maps a non-OK status to its categorized sentinel error, or nil.
func (s Sample) Err() error {
	switch s.Status {
	case StatusOK:
		return nil
	case StatusNegativeDelay:
		return ErrNegativeDelay
	case StatusZeroTimestamp:
		return ErrZeroTimestamp
	case StatusKissOfDeath:
		return ErrKissOfDeath
	case StatusUnsynchronized:
		return ErrUnsynchronized
	default:
		// Never masquerade an unknown status as success.
		return errors.New("core: unknown sample status " + string(s.Status))
	}
}

// Evaluate runs the fixed four-timestamp math on raw timestamps.
//
// It never returns an error by panic-style control flow: the returned Sample
// always carries an explicit Status, and Sample.Err yields the categorized
// failure. epsilon widens the asymmetry interval with local precision/server
// dispersion uncertainty and must be >= 0.
func Evaluate(r RawTimestamps, epsilon time.Duration) Sample {
	if epsilon < 0 {
		epsilon = 0
	}
	s := Sample{
		SourceID:       r.SourceID,
		Stratum:        r.Stratum,
		LI:             r.LI,
		KissCode:       r.KissCode,
		RootDelay:      r.RootDelay,
		RootDispersion: r.RootDispersion,
		CollectedAt:    r.CollectedAt,
		Epsilon:        epsilon,
	}

	f := r.T2.Sub(r.T1)
	b := r.T4.Sub(r.T3)
	s.Forward = f
	s.Backward = b
	s.Offset = (f - b) / 2
	s.RTT = f + b

	// Validity gates, checked in a fixed order so the reported failure category
	// is deterministic on malformed input.
	switch {
	case r.T2.IsZero() || r.T3.IsZero():
		s.Status = StatusZeroTimestamp
		s.Reason = "server receive/transmit timestamp is zero"
	case r.Stratum == 0:
		s.Status = StatusKissOfDeath
		s.Reason = "stratum 0" + kissSuffix(r.KissCode)
	case r.LI == 3:
		s.Status = StatusUnsynchronized
		s.Reason = "server leap indicator is ALARM (3)"
	case s.RTT < 0:
		s.Status = StatusNegativeDelay
		s.Reason = "delta=(t4-t1)-(t3-t2)=" + s.RTT.String() + " < 0"
	default:
		s.Status = StatusOK
	}

	// Width is exactly RTT even when it is odd at nanosecond resolution:
	// lower takes floor(delta/2), upper the remainder.
	half := s.RTT / 2
	s.Lower = s.Offset - half - epsilon
	s.Upper = s.Offset + (s.RTT - half) + epsilon
	return s
}

func kissSuffix(code string) string {
	if code == "" {
		return ""
	}
	return " kiss=" + code
}
