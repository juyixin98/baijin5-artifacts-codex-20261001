package core_test

import (
	"errors"
	"testing"
	"time"

	"ntpsim/internal/core"
)

func makeSample(id string, collected time.Time, offset, rtt time.Duration, stratum uint8) core.Sample {
	// Build a valid sample whose interval is [offset-rtt/2, offset+rtt/2].
	half := rtt / 2
	t1 := collected.Add(-time.Second)
	t2 := t1.Add(offset + half)
	t3 := t2
	t4 := t3.Add(-offset + (rtt - half))
	return core.Evaluate(core.RawTimestamps{
		SourceID: id, T1: t1, T2: t2, T3: t3, T4: t4,
		CollectedAt: collected, Stratum: stratum,
	}, 0)
}

func TestSelectHappyPath(t *testing.T) {
	now := at(10, 0)
	samples := []core.Sample{
		makeSample("a", now, 100*time.Millisecond, 40*time.Millisecond, 2),
		makeSample("b", now, 102*time.Millisecond, 60*time.Millisecond, 3),
		makeSample("c", now, 98*time.Millisecond, 20*time.Millisecond, 2),
	}
	p := core.DefaultPolicy()
	res := core.Select(samples, now, p, core.SourceHistory{})
	if res.Err() != nil {
		t.Fatalf("status=%s reason=%s", res.Status, res.Reason)
	}
	// Intersection of:
	//  a [80,120]  b [72,132]  c [88,108]  => [88,108] ms, count 3.
	if res.Lower != 88*time.Millisecond || res.Upper != 108*time.Millisecond {
		t.Fatalf("intersection=[%v,%v] want [88ms,108ms]", res.Lower, res.Upper)
	}
	if res.AcceptedCount != 3 {
		t.Fatalf("accepted=%d want 3", res.AcceptedCount)
	}
	// c is stratum 2 with shortest RTT => chosen over a (also stratum 2, rtt 40).
	if res.SourceID != "c" {
		t.Fatalf("selected=%s want c", res.SourceID)
	}
}

func TestSelectStale(t *testing.T) {
	now := at(10, 0)
	old := now.Add(-10 * time.Minute)
	samples := []core.Sample{
		makeSample("a", old, 0, 10*time.Millisecond, 2),
		makeSample("b", old, 0, 10*time.Millisecond, 2),
	}
	res := core.Select(samples, now, core.DefaultPolicy(), core.SourceHistory{})
	if res.Status != core.SelectionAllStale {
		t.Fatalf("status=%s want ALL_STALE", res.Status)
	}
	if !errors.Is(res.Err(), core.ErrAllStale) {
		t.Fatalf("err=%v", res.Err())
	}
	for _, c := range res.Candidates {
		if c.Verdict != core.VerdictStale {
			t.Fatalf("candidate %s verdict=%s want STALE", c.SourceID, c.Verdict)
		}
	}
}

func TestSelectExtremeRTT(t *testing.T) {
	now := at(11, 0)
	samples := []core.Sample{
		makeSample("slow", now, 100*time.Millisecond, 8*time.Second, 2),
	}
	res := core.Select(samples, now, core.DefaultPolicy(), core.SourceHistory{})
	if res.Status != core.SelectionInsufficientSources {
		t.Fatalf("status=%s", res.Status)
	}
	if res.Candidates[0].Verdict != core.VerdictExtremeRTT {
		t.Fatalf("verdict=%s want EXTREME_RTT", res.Candidates[0].Verdict)
	}
}

// TestSelectRTTOutlier: three sources, one with RTT far above the median.
func TestSelectRTTOutlier(t *testing.T) {
	now := at(12, 0)
	samples := []core.Sample{
		makeSample("a", now, 100*time.Millisecond, 20*time.Millisecond, 2),
		makeSample("b", now, 100*time.Millisecond, 24*time.Millisecond, 2),
		makeSample("c", now, 100*time.Millisecond, 2*time.Second, 2),
	}
	res := core.Select(samples, now, core.DefaultPolicy(), core.SourceHistory{})
	if res.Err() != nil {
		t.Fatalf("status=%s reason=%s", res.Status, res.Reason)
	}
	var cverdict string
	for _, c := range res.Candidates {
		if c.SourceID == "c" {
			cverdict = string(c.Verdict)
		}
	}
	if cverdict != string(core.VerdictOutlierRTT) {
		t.Fatalf("c verdict=%s want OUTLIER_RTT", cverdict)
	}
	if res.AcceptedCount != 2 {
		t.Fatalf("accepted=%d want 2", res.AcceptedCount)
	}
}

// TestSelectSourceDisagreement: two tight clusters far apart; intersection of
// the maximal set may still exist pairwise, so force MinSources=2 with two
// DISJOINT intervals => NO_INTERSECTION.
func TestSelectSourceDisagreement(t *testing.T) {
	now := at(13, 0)
	samples := []core.Sample{
		makeSample("a", now, 0, 10*time.Millisecond, 2),                    // [-5,+5]ms
		makeSample("b", now, 500*time.Millisecond, 10*time.Millisecond, 2), // [495,505]ms
	}
	p := core.DefaultPolicy()
	p.MinSources = 2
	res := core.Select(samples, now, p, core.SourceHistory{})
	if res.Status != core.SelectionNoIntersection {
		t.Fatalf("status=%s want NO_INTERSECTION", res.Status)
	}
	if !errors.Is(res.Err(), core.ErrNoIntersection) {
		t.Fatalf("err=%v", res.Err())
	}
}

// TestSelectClockJump: same source moves 800ms between rounds; the second
// sample must be rejected as CLOCK_JUMP.
func TestSelectClockJump(t *testing.T) {
	now := at(14, 0)
	hist := core.SourceHistory{}
	p := core.DefaultPolicy()

	first := []core.Sample{makeSample("a", now, 100*time.Millisecond, 20*time.Millisecond, 2)}
	r1 := core.Select(first, now, p, hist)
	if r1.Err() != nil {
		t.Fatalf("round 1: %s", r1.Reason)
	}
	later := now.Add(time.Minute)
	second := []core.Sample{makeSample("a", later, 900*time.Millisecond, 20*time.Millisecond, 2)}
	r2 := core.Select(second, later, p, hist)
	if r2.Status != core.SelectionClockJump {
		t.Fatalf("round 2 status=%s want CLOCK_JUMP", r2.Status)
	}
	if r2.Candidates[0].Verdict != core.VerdictClockJump {
		t.Fatalf("verdict=%s", r2.Candidates[0].Verdict)
	}
}

// TestSelectMarzulloMajority: 3 agree, 1 wild source. Marzullo must return
// the 3-source window even though all 4 technically overlap nowhere common.
func TestSelectMarzulloMajority(t *testing.T) {
	now := at(15, 0)
	samples := []core.Sample{
		makeSample("a", now, 100*time.Millisecond, 20*time.Millisecond, 2),     // [90,110]
		makeSample("b", now, 104*time.Millisecond, 20*time.Millisecond, 2),     // [94,114]
		makeSample("c", now, 96*time.Millisecond, 20*time.Millisecond, 2),      // [86,106]
		makeSample("wild", now, -500*time.Millisecond, 20*time.Millisecond, 1), // [-510,-490]
	}
	p := core.DefaultPolicy()
	p.MinSources = 3
	res := core.Select(samples, now, p, core.SourceHistory{})
	if res.Err() != nil {
		t.Fatalf("status=%s reason=%s", res.Status, res.Reason)
	}
	// Maximal agreement: intersection of a,b,c = [94,106].
	if res.Lower != 94*time.Millisecond || res.Upper != 106*time.Millisecond {
		t.Fatalf("window=[%v,%v] want [94ms,106ms]", res.Lower, res.Upper)
	}
	if res.SourceID == "wild" {
		t.Fatal("wild source must not be selected")
	}
}

func TestSelectEmpty(t *testing.T) {
	res := core.Select(nil, at(16, 0), core.DefaultPolicy(), nil)
	if res.Status != core.SelectionNoSamples {
		t.Fatalf("status=%s", res.Status)
	}
	if !errors.Is(res.Err(), core.ErrNoSamples) {
		t.Fatalf("err=%v", res.Err())
	}
}

func TestSelectBadSampleExcluded(t *testing.T) {
	now := at(17, 0)
	bad := core.Evaluate(core.RawTimestamps{
		SourceID: "bad", T1: now, T4: now.Add(-time.Second),
		CollectedAt: now, Stratum: 2,
	}, 0)
	good := makeSample("good", now, 0, 20*time.Millisecond, 2)
	res := core.Select([]core.Sample{bad, good}, now, core.DefaultPolicy(), core.SourceHistory{})
	if res.Err() != nil {
		t.Fatalf("status=%s", res.Status)
	}
	if res.SourceID != "good" {
		t.Fatalf("selected=%s want good", res.SourceID)
	}
	for _, c := range res.Candidates {
		if c.SourceID == "bad" && c.Verdict != core.VerdictSampleBad {
			t.Fatalf("bad verdict=%s", c.Verdict)
		}
	}
}
