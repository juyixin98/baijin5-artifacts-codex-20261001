// Package run orchestrates a configured simulation: it builds the virtual
// fabric, registers synthesized sources, polls them round by round, feeds
// samples to the selection engine and emits structured, correlatable logs.
//
// Every log line carries the run ID and round number, shows the four
// timestamps / computed legs and the exact selection verdict, so an input can
// be traced to the decision. Failures are logged as their real category and
// never reported as success.
package run

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"ntpsim/internal/config"
	"ntpsim/internal/core"
	"ntpsim/internal/sim"
	"ntpsim/internal/transport"
	"ntpsim/internal/version"
)

// Options controls a run.
type Options struct {
	ConfigPath string
	Output     io.Writer // log sink (defaults to os.Stdout)
	Store      Store     // optional persistence (nil = in-memory only)
}

// Summary is the per-round and overall outcome.
type Summary struct {
	RunID    string
	Rounds   int
	Selected []core.SelectionResult
	Samples  [][]core.Sample
}

// Logger is a tiny structured line logger: key=value pairs, quoted if needed.
type Logger struct {
	mu sync.Mutex
	w  io.Writer
}

func newLogger(w io.Writer) *Logger {
	if w == nil {
		w = os.Stdout
	}
	return &Logger{w: w}
}

// emit writes one log line. round 0 means run-level (not tied to a round).
func (l *Logger) emit(runID string, round int, level, msg string, kv ...any) {
	var b strings.Builder
	b.WriteString("time=")
	b.WriteString(time.Now().UTC().Format("2006-01-02T15:04:05.000000Z"))
	b.WriteString(" run=")
	b.WriteString(runID)
	b.WriteString(" round=")
	b.WriteString(strconv.Itoa(round))
	b.WriteString(" level=")
	b.WriteString(level)
	b.WriteString(" event=")
	b.WriteString(msg)
	for i := 0; i+1 < len(kv); i += 2 {
		b.WriteByte(' ')
		b.WriteString(fmt.Sprint(kv[i]))
		b.WriteByte('=')
		b.WriteString(quoteValue(kv[i+1]))
	}
	b.WriteByte('\n')
	l.mu.Lock()
	defer l.mu.Unlock()
	_, _ = io.WriteString(l.w, b.String())
}

func quoteValue(v any) string {
	s := fmt.Sprint(v)
	if s == "" {
		return `""`
	}
	if strings.ContainsAny(s, " \t\"=\n") {
		return strconv.Quote(s)
	}
	return s
}

// newRunID returns a short random correlation identifier.
func newRunID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "run-" + hex.EncodeToString(b[:])
}

// Execute loads the config, runs all rounds and returns the summary.
func Execute(opts Options) (*Summary, error) {
	cfg, err := config.Load(opts.ConfigPath)
	if err != nil {
		return nil, err
	}
	return ExecuteConfig(cfg, opts)
}

// ExecuteConfig runs an already-validated config (used by tests directly).
func ExecuteConfig(cfg *config.Config, opts Options) (*Summary, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	runID := newRunID()
	lg := newLogger(opts.Output)
	start := cfg.StartTime()

	logf := func(round int, level, msg string, kv ...any) {
		lg.emit(runID, round, level, msg, kv...)
	}

	logf(0, "INFO", "run_start",
		"ntpsim_version", version.Version,
		"ntp_version", version.ProtocolVersion,
		"start", start.Format(time.RFC3339Nano),
		"rounds", cfg.Run.Rounds,
		"sources", len(cfg.Sources))

	links := sim.NewAsymLink(cfg.Run.LinkSeed)
	env := sim.NewEnvironment(start, links)
	defer env.Stop()
	clientEP := env.Endpoint("client")
	clientClock := sim.NewEnvClock(env)
	client := transport.NewClient(clientClock)

	for _, sc := range cfg.Sources {
		spec := sim.SourceSpec{
			Name:        sc.Name,
			ClockOffset: sc.Offset.Duration(),
			DriftPPM:    sc.DriftPPM,
			Profile:     sc.ServerProfile(),
		}
		if sc.StepAt != "" {
			t, _ := time.Parse(time.RFC3339, sc.StepAt)
			spec.StepAt = t
			spec.StepAmount = sc.StepOffset.Duration()
		}
		sim.RegisterSource(env, start, spec)
	}
	for _, lc := range cfg.Links {
		links.SetDelay("client", lc.Source, lc.ToServer.Duration())
		links.SetDelay(lc.Source, "client", lc.FromServer.Duration())
		if lc.LossRate > 0 {
			links.SetLoss("client", lc.Source, lc.LossRate)
			links.SetLoss(lc.Source, "client", lc.LossRate)
		}
		if lc.DupRate > 0 {
			links.SetDuplication("client", lc.Source, lc.DupRate)
			links.SetDuplication(lc.Source, "client", lc.DupRate)
		}
	}

	policy := cfg.PolicyOptions()
	hist := core.SourceHistory{}
	sum := &Summary{RunID: runID, Rounds: cfg.Run.Rounds}
	exCfg := transport.ExchangeConfig{
		Timeout: cfg.Run.ReplyTimeout.Duration(),
		Epsilon: cfg.Run.Epsilon.Duration(),
	}

	for r := 1; r <= cfg.Run.Rounds; r++ {
		if r > 1 {
			env.Sleep(cfg.Run.PollInterval.Duration())
		}
		now := env.Now()
		logf(r, "INFO", "round_start", "virtual_time", now.Format(time.RFC3339Nano))

		// Sequential per-source exchanges keep the single client mailbox
		// unambiguous; virtual time makes this cost-free.
		samples := make([]core.Sample, 0, len(cfg.Sources))
		for _, sc := range cfg.Sources {
			s, err := client.Exchange(clientEP, sc.Name, exCfg)
			if err != nil {
				logf(r, "ERROR", "exchange_failed",
					"source", sc.Name,
					"category", errorCategory(err),
					"error", err.Error())
				continue
			}
			samples = append(samples, s)
			logSample(logf, r, s)
		}

		sel := core.Select(samples, env.Now(), policy, hist)
		sum.Samples = append(sum.Samples, append([]core.Sample(nil), samples...))
		sum.Selected = append(sum.Selected, sel)

		logSelection(logf, r, sel)
		if opts.Store != nil {
			if err := opts.Store.SaveRound(runID, r, env.Now(), samples, sel); err != nil {
				logf(r, "ERROR", "store_failed", "error", err.Error())
			}
		}
		if sel.Err() != nil {
			logf(r, "WARN", "round_no_sync",
				"status", string(sel.Status), "reason", sel.Reason)
		} else {
			logf(r, "INFO", "round_synced",
				"selected", sel.SourceID,
				"offset", sel.Offset.String(),
				"interval", "["+sel.Lower.String()+","+sel.Upper.String()+"]",
				"sources", sel.AcceptedCount)
		}
	}
	logf(0, "INFO", "run_end", "rounds", cfg.Run.Rounds)
	return sum, nil
}

func logSample(logf func(int, string, string, ...any), r int, s core.Sample) {
	if s.OK() {
		logf(r, "INFO", "sample",
			"source", s.SourceID,
			"status", string(s.Status),
			"forward_leg_f=t2-t1", s.Forward.String(),
			"backward_leg_b=t4-t3", s.Backward.String(),
			"offset_theta=(f-b)/2", s.Offset.String(),
			"rtt_delta=f+b", s.RTT.String(),
			"true_offset_interval", "["+s.Lower.String()+","+s.Upper.String()+"]",
			"stratum", int(s.Stratum))
		return
	}
	logf(r, "WARN", "sample_rejected",
		"source", s.SourceID,
		"status", string(s.Status),
		"reason", s.Reason,
		"rtt", s.RTT.String(),
		"offset", s.Offset.String())
}

func logSelection(logf func(int, string, string, ...any), r int, sel core.SelectionResult) {
	for _, c := range sel.Candidates {
		logf(r, "INFO", "candidate",
			"source", c.SourceID,
			"verdict", string(c.Verdict),
			"offset", c.Offset.String(),
			"rtt", c.RTT.String(),
			"interval", "["+c.Lower.String()+","+c.Upper.String()+"]",
			"detail", c.Detail)
	}
	logf(r, "INFO", "selection",
		"status", string(sel.Status),
		"reason", sel.Reason,
		"accepted_sources", sel.AcceptedCount)
}

// errorCategory maps a wrapped transport error to a stable short label.
func errorCategory(err error) string {
	msg := err.Error()
	switch {
	case strings.Contains(msg, "stale/replayed"):
		return "STALE_OR_REPLAY"
	case strings.Contains(msg, "origin timestamp mismatch"):
		return "ORIGIN_MISMATCH"
	case strings.Contains(msg, "no reply"):
		return "TIMEOUT"
	case strings.Contains(msg, "malformed"):
		return "MALFORMED"
	case strings.Contains(msg, "unexpected peer"):
		return "WRONG_PEER"
	default:
		return "TRANSPORT"
	}
}
