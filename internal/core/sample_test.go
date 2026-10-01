package core_test

import (
	"errors"
	"testing"
	"time"

	"ntpsim/internal/core"
)

// anchor is a fixed origin for all hand-computed timestamps.
var anchor = time.Date(2024, 6, 1, 12, 0, 0, 0, time.UTC)

func at(offsetSecs int, nanos int) time.Time {
	return anchor.Add(time.Duration(offsetSecs)*time.Second + time.Duration(nanos))
}

// TestSymmetricHandCalc is the textbook symmetric path.
//
//	true client->server delay = server->client delay = 100 ms
//	server clock 250 ms AHEAD of client.
//
//	t1 = 0.000           (client)
//	t2 = 0.350           (server receive: 100ms link + 250ms offset)
//	t3 = 0.350           (server transmit, no processing gap)
//	t4 = 0.200           (client receive: 100ms link after t3-server-time
//	                      translated back to client clock: 0.350-0.250+0.100)
//
//	f  = t2-t1 = 350ms; b = t4-t3 = -150ms
//	theta = (350-(-150))/2 = +250ms   (server ahead)
//	delta = 350+(-150) = 200ms
func TestSymmetricHandCalc(t *testing.T) {
	r := core.RawTimestamps{
		SourceID:    "s1",
		T1:          at(0, 0),
		T2:          at(0, 350_000_000),
		T3:          at(0, 350_000_000),
		T4:          at(0, 200_000_000),
		CollectedAt: at(0, 200_000_000),
		Stratum:     2,
	}
	s := core.Evaluate(r, 0)
	if s.Status != core.StatusOK {
		t.Fatalf("status=%s reason=%s", s.Status, s.Reason)
	}
	if s.Forward != 350*time.Millisecond || s.Backward != -150*time.Millisecond {
		t.Fatalf("legs f=%v b=%v", s.Forward, s.Backward)
	}
	if s.Offset != 250*time.Millisecond {
		t.Fatalf("offset=%v want +250ms", s.Offset)
	}
	if s.RTT != 200*time.Millisecond {
		t.Fatalf("rtt=%v want 200ms", s.RTT)
	}
	// Symmetric path: true offset sits exactly at interval center; the
	// RTT-wide interval is [theta-delta/2, theta+delta/2] = [150ms,350ms].
	if s.Lower != 150*time.Millisecond || s.Upper != 350*time.Millisecond {
		t.Fatalf("interval=[%v,%v] want [150ms,350ms]", s.Lower, s.Upper)
	}
	if s.Err() != nil {
		t.Fatalf("Err should be nil, got %v", s.Err())
	}
}

// TestServerBehindSign checks the offset sign convention on the other side:
// negative theta means server clock is BEHIND.
func TestServerBehindSign(t *testing.T) {
	// Server 40ms behind, symmetric 10ms legs:
	// f = 10-40 = -30ms ; b = 10+40 = +50ms
	r := core.RawTimestamps{
		T1: at(1, 0), T2: at(1, -30_000_000),
		T3: at(1, -30_000_000), T4: at(1, 20_000_000),
		CollectedAt: at(1, 20_000_000), Stratum: 2,
	}
	s := core.Evaluate(r, 0)
	if s.Offset != -40*time.Millisecond {
		t.Fatalf("offset=%v want -40ms", s.Offset)
	}
	if s.RTT != 20*time.Millisecond {
		t.Fatalf("rtt=%v want 20ms", s.RTT)
	}
	if s.Lower != -50*time.Millisecond || s.Upper != -30*time.Millisecond {
		t.Fatalf("interval=[%v,%v] want [-50ms,-30ms]", s.Lower, s.Upper)
	}
}

// TestAsymmetryBoundsTrueOffset is the key acceptance test: with an
// asymmetric path the single-sample estimate is biased by exactly half the
// delay difference, and the true offset MUST remain inside the returned
// interval for every split of the forward/backward delay.
//
// Fixed observed values: f=210ms, b=30ms => theta=90ms, delta=240ms.
// We enumerate every true asymmetry consistent with f,b and a 90ms offset and
// verify containment: [theta-delta/2, theta+delta/2] = [-30ms, 210ms].
func TestAsymmetryBoundsTrueOffset(t *testing.T) {
	r := core.RawTimestamps{
		T1: at(2, 0), T2: at(2, 210_000_000),
		T3: at(2, 210_000_000), T4: at(2, 240_000_000),
		CollectedAt: at(2, 240_000_000), Stratum: 2,
	}
	s := core.Evaluate(r, 0)
	if s.Offset != 90*time.Millisecond || s.RTT != 240*time.Millisecond {
		t.Fatalf("theta=%v delta=%v", s.Offset, s.RTT)
	}
	if s.Lower != -30*time.Millisecond || s.Upper != 210*time.Millisecond {
		t.Fatalf("interval=[%v,%v] want [-30ms,210ms]", s.Lower, s.Upper)
	}

	// Model: with true offset o and true legs d_f,d_b >= 0:
	//   f = d_f + o ; b = d_b - o ; d_f + d_b = delta
	// => d_f in [0,240ms], o = f - d_f = 210ms - d_f  => o in [-30ms,210ms].
	for df := 0; df <= 240; df++ {
		trueOffset := 210*time.Millisecond - time.Duration(df)*time.Millisecond
		if trueOffset < s.Lower || trueOffset > s.Upper {
			t.Fatalf("true offset %v (d_f=%dms) escaped interval", trueOffset, df)
		}
	}
}

// TestEpsilonWidening checks server/local uncertainty widens the interval
// symmetrically without moving the point estimate.
func TestEpsilonWidening(t *testing.T) {
	// Symmetric 50ms legs, zero offset: f=50ms, b=50ms => theta=0, delta=100ms.
	r := core.RawTimestamps{
		T1: at(3, 0), T2: at(3, 50_000_000),
		T3: at(3, 50_000_000), T4: at(3, 100_000_000),
		CollectedAt: at(3, 100_000_000), Stratum: 2,
	}
	s := core.Evaluate(r, 5*time.Millisecond)
	if s.Offset != 0 || s.RTT != 100*time.Millisecond {
		t.Fatalf("theta=%v delta=%v", s.Offset, s.RTT)
	}
	if s.Lower != -55*time.Millisecond || s.Upper != 55*time.Millisecond {
		t.Fatalf("widened interval=[%v,%v] want [-55ms,55ms]", s.Lower, s.Upper)
	}
}

// TestNegativeDelay constructs the impossible case delta < 0: the client
// received the reply before the server could have sent it (relative to the
// same frame). Must be classified NEGATIVE_DELAY, never OK.
func TestNegativeDelay(t *testing.T) {
	r := core.RawTimestamps{
		T1: at(4, 0), T2: at(4, 100_000_000),
		T3: at(4, 100_000_000), T4: at(4, 50_000_000), // t4 < t1+... => f+b = -50ms?
		CollectedAt: at(4, 50_000_000), Stratum: 2,
	}
	// f=+100ms, b = t4-t3 = -50ms => delta = +50ms actually; make t4 earlier:
	r.T4 = at(3, 900_000_000)
	r.CollectedAt = r.T4
	// f=+100ms, b = -200ms => delta = -100ms < 0
	s := core.Evaluate(r, 0)
	if s.Status != core.StatusNegativeDelay {
		t.Fatalf("status=%s want NEGATIVE_DELAY", s.Status)
	}
	if !errors.Is(s.Err(), core.ErrNegativeDelay) {
		t.Fatalf("Err()=%v want ErrNegativeDelay", s.Err())
	}
	if s.OK() {
		t.Fatal("negative-delay sample must not be OK")
	}
}

// TestKissAndUnsynchronized verify the semantic failure categories.
func TestKissAndUnsynchronized(t *testing.T) {
	r := core.RawTimestamps{
		T1: at(5, 0), T2: at(5, 50_000_000), T3: at(5, 50_000_000),
		T4: at(5, 60_000_000), CollectedAt: at(5, 60_000_000),
		Stratum: 0, KissCode: "DENY",
	}
	s := core.Evaluate(r, 0)
	if s.Status != core.StatusKissOfDeath || !errors.Is(s.Err(), core.ErrKissOfDeath) {
		t.Fatalf("kod status=%s err=%v", s.Status, s.Err())
	}

	r.Stratum = 2
	r.LI = 3
	s = core.Evaluate(r, 0)
	if s.Status != core.StatusUnsynchronized || !errors.Is(s.Err(), core.ErrUnsynchronized) {
		t.Fatalf("li status=%s err=%v", s.Status, s.Err())
	}
}

// TestZeroTimestamp ensures missing server stamps are a distinct failure.
func TestZeroTimestamp(t *testing.T) {
	r := core.RawTimestamps{
		T1: at(6, 0), T4: at(6, 10_000_000),
		CollectedAt: at(6, 10_000_000), Stratum: 2,
	}
	s := core.Evaluate(r, 0)
	if s.Status != core.StatusZeroTimestamp {
		t.Fatalf("status=%s want ZERO_TIMESTAMP", s.Status)
	}
	if !errors.Is(s.Err(), core.ErrZeroTimestamp) {
		t.Fatalf("err=%v", s.Err())
	}
}

// TestOddRTTRounding checks odd nanosecond RTT splits without overflow.
func TestOddRTTRounding(t *testing.T) {
	r := core.RawTimestamps{
		T1: at(7, 0), T2: at(7, 101),
		T3: at(7, 101), T4: at(7, 100),
		CollectedAt: at(7, 100), Stratum: 2,
	}
	s := core.Evaluate(r, 0)
	// theta = (101 - (-1))/2 = 51ns ; delta = 100ns; interval width must be 100.
	if s.Offset != 51 || s.RTT != 100 {
		t.Fatalf("theta=%d rtt=%d", s.Offset, s.RTT)
	}
	if width := s.Upper - s.Lower; width != 100 {
		t.Fatalf("interval width=%d want 100", width)
	}
}
