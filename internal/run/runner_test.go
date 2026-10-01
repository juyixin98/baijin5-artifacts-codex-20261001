package run_test

import (
	"strings"
	"testing"
	"time"

	"ntpsim/internal/config"
	"ntpsim/internal/core"
	"ntpsim/internal/run"
)

func baseConfig() *config.Config {
	return &config.Config{
		Sources: []config.SourceConfig{
			{Name: "a", Stratum: 2, LI: 0, ReferenceID: 1, Offset: config.Duration(100 * time.Millisecond)},
			{Name: "b", Stratum: 2, LI: 0, ReferenceID: 2, Offset: config.Duration(105 * time.Millisecond)},
			{Name: "c", Stratum: 3, LI: 0, ReferenceID: 3, Offset: config.Duration(97 * time.Millisecond)},
		},
		Links: []config.LinkConfig{
			{Source: "a", ToServer: config.Duration(20 * time.Millisecond), FromServer: config.Duration(30 * time.Millisecond)},
			{Source: "b", ToServer: config.Duration(25 * time.Millisecond), FromServer: config.Duration(25 * time.Millisecond)},
			{Source: "c", ToServer: config.Duration(40 * time.Millisecond), FromServer: config.Duration(20 * time.Millisecond)},
		},
		Policy: config.PolicyConfig{
			MaxSampleAge:     config.Duration(90 * time.Second),
			MaxRTT:           config.Duration(2 * time.Second),
			MaxClockJump:     config.Duration(500 * time.Millisecond),
			RTTOutlierFactor: 3,
			MinSources:       1,
		},
		Run: config.RunConfig{
			Start:        "2024-06-01T12:00:00Z",
			Rounds:       3,
			PollInterval: config.Duration(30 * time.Second),
			ReplyTimeout: config.Duration(2 * time.Second),
			Epsilon:      config.Duration(100 * time.Microsecond),
			LinkSeed:     7,
		},
	}
}

// TestRunHealthy: three agreeing sources synchronize every round and the
// selected source lies at the lowest stratum; offsets sit inside the
// intersection, near the true ~100ms offsets.
func TestRunHealthy(t *testing.T) {
	var sb strings.Builder
	sum, err := run.ExecuteConfig(baseConfig(), run.Options{Output: &sb})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if len(sum.Selected) != 3 {
		t.Fatalf("rounds=%d want 3", len(sum.Selected))
	}
	for i, sel := range sum.Selected {
		if sel.Err() != nil {
			t.Fatalf("round %d: %s: %s", i+1, sel.Status, sel.Reason)
		}
		if sel.SourceID != "a" && sel.SourceID != "b" {
			t.Fatalf("round %d picked stratum-3 source %s", i+1, sel.SourceID)
		}
		// True offsets are within [97,105]ms; intersection must contain that band.
		if sel.Lower > 97*time.Millisecond || sel.Upper < 105*time.Millisecond {
			t.Fatalf("round %d interval [%v,%v] does not contain true band", i+1, sel.Lower, sel.Upper)
		}
	}
	logs := sb.String()
	// Every line is correlatable to one run id and shows version at start.
	if !strings.Contains(logs, "ntpsim_version=") || !strings.Contains(logs, "event=run_start") {
		t.Fatal("logs missing run_start/version")
	}
	if !strings.Contains(logs, "event=round_synced") {
		t.Fatal("logs missing round_synced")
	}
	if c := strings.Count(logs, "run=run-"); c < 9 {
		t.Fatalf("only %d correlated log lines", c)
	}
}

// TestRunExtremeRTTRejected: source c gets a 3s RTT; hard gate rejects it but
// the other two still synchronize, and the verdict is explicit.
func TestRunExtremeRTTRejected(t *testing.T) {
	cfg := baseConfig()
	cfg.Links[2] = config.LinkConfig{
		Source: "c", ToServer: config.Duration(1500 * time.Millisecond), FromServer: config.Duration(1500 * time.Millisecond),
	}
	// Reply timeout must exceed the 3s RTT so the sample is actually produced
	// and then rejected by the selection policy's hard RTT gate.
	cfg.Run.ReplyTimeout = config.Duration(8 * time.Second)
	cfg.Run.Rounds = 1
	var sb strings.Builder
	sum, err := run.ExecuteConfig(cfg, run.Options{Output: &sb})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	sel := sum.Selected[0]
	if sel.Err() != nil {
		t.Fatalf("should still sync on a,b: %s", sel.Reason)
	}
	var cVerdict string
	for _, cand := range sel.Candidates {
		if cand.SourceID == "c" {
			cVerdict = string(cand.Verdict)
		}
	}
	if cVerdict != string(core.VerdictExtremeRTT) {
		t.Fatalf("c verdict=%s want EXTREME_RTT", cVerdict)
	}
	if !strings.Contains(sb.String(), "verdict=EXTREME_RTT") {
		t.Fatal("logs must record the explicit rejection")
	}
}

// TestRunTimeoutSource: a source whose replies are 100% lost is reported with
// the TIMEOUT category, excluded, and never faked as success.
func TestRunTimeoutSource(t *testing.T) {
	cfg := baseConfig()
	cfg.Links[1].LossRate = 1.0 // b never replies
	cfg.Run.Rounds = 1
	var sb strings.Builder
	sum, err := run.ExecuteConfig(cfg, run.Options{Output: &sb})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	sel := sum.Selected[0]
	if sel.Err() != nil {
		t.Fatalf("a and c should still sync: %s", sel.Reason)
	}
	if !strings.Contains(sb.String(), "category=TIMEOUT") {
		t.Fatalf("logs missing TIMEOUT category:\n%s", sb.String())
	}
	// b must not appear among evaluated samples.
	for _, sm := range sum.Samples[0] {
		if sm.SourceID == "b" {
			t.Fatal("timed-out source b must not produce a sample")
		}
	}
}

// TestRunAllSourcesDisagree: two sources with disjoint offsets and MinSources=2
// must end in NO_INTERSECTION rather than inventing a sync.
func TestRunAllSourcesDisagree(t *testing.T) {
	cfg := baseConfig()
	cfg.Sources = []config.SourceConfig{
		{Name: "a", Stratum: 2, Offset: config.Duration(0)},
		{Name: "b", Stratum: 2, Offset: config.Duration(800 * time.Millisecond)},
	}
	cfg.Links = []config.LinkConfig{
		{Source: "a", ToServer: config.Duration(5 * time.Millisecond), FromServer: config.Duration(5 * time.Millisecond)},
		{Source: "b", ToServer: config.Duration(5 * time.Millisecond), FromServer: config.Duration(5 * time.Millisecond)},
	}
	cfg.Policy.MinSources = 2
	cfg.Run.Rounds = 1
	sum, err := run.ExecuteConfig(cfg, run.Options{Output: discard{}})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if sum.Selected[0].Status != core.SelectionNoIntersection {
		t.Fatalf("status=%s want NO_INTERSECTION", sum.Selected[0].Status)
	}
}

// TestRunClockJumpRejected: one source jumps 800ms mid-run; the post-jump
// round rejects it as CLOCK_JUMP.
func TestRunClockJumpRejected(t *testing.T) {
	cfg := baseConfig()
	cfg.Sources = []config.SourceConfig{
		{Name: "a", Stratum: 2, Offset: config.Duration(100 * time.Millisecond),
			StepAt: "2024-06-01T12:00:45Z", StepOffset: config.Duration(800 * time.Millisecond)},
	}
	cfg.Links = []config.LinkConfig{
		{Source: "a", ToServer: config.Duration(10 * time.Millisecond), FromServer: config.Duration(10 * time.Millisecond)},
	}
	cfg.Run.Rounds = 3
	cfg.Run.PollInterval = config.Duration(30 * time.Second)
	sum, err := run.ExecuteConfig(cfg, run.Options{Output: discard{}})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	// Round 1 (t=0) syncs; rounds 2 (t=30, pre-step but history matches) syncs;
	// round 3 (t=60) sees the jump and must NOT sync on this single source.
	if sum.Selected[0].Err() != nil {
		t.Fatalf("round1 should sync: %s", sum.Selected[0].Reason)
	}
	last := sum.Selected[2]
	if last.Status != core.SelectionClockJump {
		t.Fatalf("round3 status=%s want CLOCK_JUMP", last.Status)
	}
}

type discard struct{}

func (discard) Write(p []byte) (int, error) { return len(p), nil }
