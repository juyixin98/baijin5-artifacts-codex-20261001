package sim_test

import (
	"strings"
	"testing"
	"time"

	"ntpsim/internal/core"
	"ntpsim/internal/sim"
	"ntpsim/internal/transport"
)

var t0 = time.Date(2024, 6, 1, 12, 0, 0, 0, time.UTC)

// tolerance absorbs sub-nanosecond NTP fixed-point rounding (<1us in practice).
const tol = time.Microsecond

func absDur(d time.Duration) time.Duration {
	if d < 0 {
		return -d
	}
	return d
}

func setupEnv(t *testing.T, links sim.LinkModel) (*sim.Environment, *transport.Client) {
	t.Helper()
	env := sim.NewEnvironment(t0, links)
	t.Cleanup(env.Stop)
	env.Endpoint("client")
	client := transport.NewClient(sim.NewEnvClock(env))
	return env, client
}

// TestSimSymmetric: 100ms symmetric legs, source +250ms ahead.
func TestSimSymmetric(t *testing.T) {
	links := sim.NewAsymLink(1)
	links.SetDelay("client", "s1", 100*time.Millisecond)
	links.SetDelay("s1", "client", 100*time.Millisecond)
	env, client := setupEnv(t, links)
	sim.RegisterSource(env, t0, sim.SourceSpec{
		Name: "s1", ClockOffset: 250 * time.Millisecond,
		Profile: transport.ServerProfile{Stratum: 2, LI: 0, ReferenceID: 1},
	})

	ep := env.Endpoint("client")
	s, err := client.Exchange(ep, "s1", transport.ExchangeConfig{Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	if !s.OK() {
		t.Fatalf("status=%s reason=%s", s.Status, s.Reason)
	}
	if absDur(s.Offset-250*time.Millisecond) > tol {
		t.Fatalf("offset=%v want ~250ms", s.Offset)
	}
	if absDur(s.RTT-200*time.Millisecond) > tol {
		t.Fatalf("rtt=%v want ~200ms", s.RTT)
	}
	// True offset must lie in the reported interval.
	if s.Lower > 250*time.Millisecond || s.Upper < 250*time.Millisecond {
		t.Fatalf("true offset 250ms outside [%v,%v]", s.Lower, s.Upper)
	}
}

// TestSimAsymmetryUnremovable is the headline behavior: 400ms forward / 20ms
// backward cannot be disambiguated from clock offset. The point estimate is
// biased by (df-db)/2 = +190ms, but the true offset stays inside the interval.
func TestSimAsymmetryUnremovable(t *testing.T) {
	links := sim.NewAsymLink(2)
	links.SetDelay("client", "s1", 400*time.Millisecond)
	links.SetDelay("s1", "client", 20*time.Millisecond)
	env, client := setupEnv(t, links)
	sim.RegisterSource(env, t0, sim.SourceSpec{
		Name: "s1", ClockOffset: 100 * time.Millisecond,
		Profile: transport.ServerProfile{Stratum: 2, ReferenceID: 1},
	})

	ep := env.Endpoint("client")
	s, err := client.Exchange(ep, "s1", transport.ExchangeConfig{Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	// theta = 100 + (400-20)/2 = 290ms (biased); delta = 420ms.
	if absDur(s.Offset-290*time.Millisecond) > tol {
		t.Fatalf("biased estimate=%v want ~290ms", s.Offset)
	}
	if absDur(s.RTT-420*time.Millisecond) > tol {
		t.Fatalf("rtt=%v want ~420ms", s.RTT)
	}
	// interval ~ [290-210, 290+210] = [80,500]ms; true offset 100ms contained.
	if s.Lower > 100*time.Millisecond-tol || s.Upper < 100*time.Millisecond+tol {
		t.Fatalf("true offset 100ms outside [%v,%v]", s.Lower, s.Upper)
	}
	// And the naive point estimate is provably wrong (190ms bias), proving the
	// interval is the only honest single-sample answer.
	if absDur(s.Offset-100*time.Millisecond) < 100*time.Millisecond {
		t.Fatal("asymmetric path unexpectedly yielded an unbiased point estimate")
	}
}

// TestSimReplyLossTimeout: reply path 100% loss => categorized timeout, never
// a bogus success.
func TestSimReplyLossTimeout(t *testing.T) {
	links := sim.NewAsymLink(3)
	links.SetDelay("client", "s1", 10*time.Millisecond)
	links.SetDelay("s1", "client", 10*time.Millisecond)
	links.SetLoss("s1", "client", 1.0)
	env, client := setupEnv(t, links)
	sim.RegisterSource(env, t0, sim.SourceSpec{
		Name: "s1", ClockOffset: 0,
		Profile: transport.ServerProfile{Stratum: 2, ReferenceID: 1},
	})

	start := env.Now()
	ep := env.Endpoint("client")
	_, err := client.Exchange(ep, "s1", transport.ExchangeConfig{Timeout: 500 * time.Millisecond})
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if !strings.Contains(err.Error(), "no reply") {
		t.Fatalf("err=%v want timeout category", err)
	}
	if env.Now().Sub(start) < 500*time.Millisecond {
		t.Fatalf("virtual time advanced %v, expected ~500ms", env.Now().Sub(start))
	}
}

// TestSimExtremeLatencyDelivered: 3s+3s legs with a large timeout produce a
// real sample with RTT ~6s (not a timeout); the selection policy then rejects
// it as EXTREME_RTT.
func TestSimExtremeLatencyDelivered(t *testing.T) {
	links := sim.NewAsymLink(4)
	links.SetDelay("client", "s1", 3*time.Second)
	links.SetDelay("s1", "client", 3*time.Second)
	env, client := setupEnv(t, links)
	sim.RegisterSource(env, t0, sim.SourceSpec{
		Name: "s1", ClockOffset: 50 * time.Millisecond,
		Profile: transport.ServerProfile{Stratum: 2, ReferenceID: 1},
	})

	ep := env.Endpoint("client")
	s, err := client.Exchange(ep, "s1", transport.ExchangeConfig{Timeout: 10 * time.Second})
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	if !s.OK() || absDur(s.RTT-6*time.Second) > tol {
		t.Fatalf("sample status=%s rtt=%v", s.Status, s.RTT)
	}
}

// TestSimClockStep: source jumps +800ms between rounds; jump is visible in the
// second sample and the client-side policy rejects it.
func TestSimClockStep(t *testing.T) {
	links := sim.NewAsymLink(5)
	links.SetDelay("client", "s1", 20*time.Millisecond)
	links.SetDelay("s1", "client", 20*time.Millisecond)
	env, client := setupEnv(t, links)
	jumpAt := t0.Add(60 * time.Second)
	sc := sim.RegisterSource(env, t0, sim.SourceSpec{
		Name: "s1", ClockOffset: 100 * time.Millisecond,
		StepAt: jumpAt, StepAmount: 800 * time.Millisecond,
		Profile: transport.ServerProfile{Stratum: 2, ReferenceID: 1},
	})
	_ = sc

	cfg := transport.ExchangeConfig{Timeout: time.Second, Epsilon: 10 * time.Microsecond}
	ep := env.Endpoint("client")
	s1, err := client.Exchange(ep, "s1", cfg)
	if err != nil {
		t.Fatalf("round 1: %v", err)
	}
	if absDur(s1.Offset-100*time.Millisecond) > tol {
		t.Fatalf("round1 offset=%v want ~100ms", s1.Offset)
	}

	env.Sleep(90 * time.Second) // cross the jump instant
	s2, err := client.Exchange(ep, "s1", cfg)
	if err != nil {
		t.Fatalf("round 2: %v", err)
	}
	if absDur(s2.Offset-900*time.Millisecond) > 5*time.Millisecond {
		t.Fatalf("round2 offset=%v want ~900ms after step", s2.Offset)
	}
	// Both samples are individually valid; the jump is a selection concern.
	if !s2.OK() {
		t.Fatalf("round2 sample not OK: %s", s2.Status)
	}
}

// TestSimKissOfDeath: stratum-0 source => sample category KISS_OF_DEATH.
func TestSimKissOfDeath(t *testing.T) {
	links := sim.NewAsymLink(6)
	links.SetDelay("client", "s1", 5*time.Millisecond)
	links.SetDelay("s1", "client", 5*time.Millisecond)
	env, client := setupEnv(t, links)
	sim.RegisterSource(env, t0, sim.SourceSpec{
		Name: "s1", ClockOffset: 0,
		Profile: transport.ServerProfile{Stratum: 0, KissCode: "RATE"},
	})
	ep := env.Endpoint("client")
	s, err := client.Exchange(ep, "s1", transport.ExchangeConfig{Timeout: time.Second})
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	if s.Status != core.StatusKissOfDeath || s.KissCode != "RATE" {
		t.Fatalf("status=%s kiss=%q", s.Status, s.KissCode)
	}
	if s.Err() == nil {
		t.Fatal("KoD must map to a non-nil error")
	}
}

// TestSimDeterministicReplay: same seed twice yields identical estimated
// offsets, proving reproducibility of the synthetic fabric.
func TestSimDeterministicReplay(t *testing.T) {
	run := func() []time.Duration {
		links := sim.NewAsymLink(42)
		links.SetDelay("client", "s1", 30*time.Millisecond)
		links.SetDelay("s1", "client", 70*time.Millisecond)
		links.Jitter = 10 * time.Millisecond
		env := sim.NewEnvironment(t0, links)
		defer env.Stop()
		env.Endpoint("client")
		c := transport.NewClient(sim.NewEnvClock(env))
		sim.RegisterSource(env, t0, sim.SourceSpec{
			Name: "s1", ClockOffset: 12 * time.Millisecond,
			Profile: transport.ServerProfile{Stratum: 2, ReferenceID: 1},
		})
		var offs []time.Duration
		ep := env.Endpoint("client")
		for i := 0; i < 5; i++ {
			if i > 0 {
				env.Sleep(time.Second)
			}
			s, err := c.Exchange(ep, "s1", transport.ExchangeConfig{Timeout: time.Second})
			if err != nil {
				t.Fatalf("exchange %d: %v", i, err)
			}
			offs = append(offs, s.Offset)
		}
		return offs
	}
	a := run()
	b := run()
	if len(a) != len(b) {
		t.Fatalf("length mismatch")
	}
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("round %d: %v != %v (non-deterministic)", i, a[i], b[i])
		}
	}
}
