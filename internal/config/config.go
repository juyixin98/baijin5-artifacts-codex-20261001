// Package config defines the file-based configuration of the simulation:
// sources with clock offsets/steps, asymmetric link delays, the selection
// policy and the polling schedule. Loading validates at the boundary so the
// rest of the program never sees partial or contradictory settings.
package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"ntpsim/internal/core"
	"ntpsim/internal/transport"
)

// Duration wraps time.Duration for JSON (un)marshalling as e.g. "150ms".
type Duration time.Duration

// Duration returns the underlying duration.
func (d Duration) Duration() time.Duration { return time.Duration(d) }

// MarshalJSON implements json.Marshaler.
func (d Duration) MarshalJSON() ([]byte, error) {
	return json.Marshal(time.Duration(d).String())
}

// UnmarshalJSON implements json.Unmarshaler.
func (d *Duration) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		return err
	}
	v, err := time.ParseDuration(s)
	if err != nil {
		return fmt.Errorf("invalid duration %q: %w", s, err)
	}
	*d = Duration(v)
	return nil
}

// SourceConfig configures one synthesized NTP source.
type SourceConfig struct {
	Name        string   `json:"name"`
	Stratum     uint8    `json:"stratum"`
	LI          uint8    `json:"li"`
	ReferenceID uint32   `json:"reference_id"`
	KissCode    string   `json:"kiss_code,omitempty"`
	Offset      Duration `json:"offset"`
	DriftPPM    int64    `json:"drift_ppm,omitempty"`
	StepAt      string   `json:"step_at,omitempty"` // RFC3339, relative to run start
	StepOffset  Duration `json:"step_offset,omitempty"`
}

// LinkConfig gives the two one-way delays and optional loss for a source.
type LinkConfig struct {
	Source     string   `json:"source"`
	ToServer   Duration `json:"to_server"`   // client -> source
	FromServer Duration `json:"from_server"` // source -> client
	LossRate   float64  `json:"loss_rate,omitempty"`
	DupRate    float64  `json:"dup_rate,omitempty"`
}

// PolicyConfig mirrors core.SelectionPolicy.
type PolicyConfig struct {
	MaxSampleAge     Duration `json:"max_sample_age"`
	MaxRTT           Duration `json:"max_rtt"`
	MaxClockJump     Duration `json:"max_clock_jump"`
	RTTOutlierFactor float64  `json:"rtt_outlier_factor"`
	MinSources       int      `json:"min_sources"`
}

// RunConfig controls the polling schedule.
type RunConfig struct {
	Start        string   `json:"start"` // RFC3339 anchor
	Rounds       int      `json:"rounds"`
	PollInterval Duration `json:"poll_interval"`
	ReplyTimeout Duration `json:"reply_timeout"`
	Epsilon      Duration `json:"epsilon"`
	LinkSeed     int64    `json:"link_seed"`
}

// Config is the root configuration document.
type Config struct {
	Sources []SourceConfig `json:"sources"`
	Links   []LinkConfig   `json:"links"`
	Policy  PolicyConfig   `json:"policy"`
	Run     RunConfig      `json:"run"`
}

// Load reads, parses and validates a configuration file.
func Load(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config: read %s: %w", path, err)
	}
	var c Config
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("config: parse %s: %w", path, err)
	}
	if err := c.Validate(); err != nil {
		return nil, fmt.Errorf("config: %w", err)
	}
	return &c, nil
}

// Validate enforces cross-field constraints.
func (c *Config) Validate() error {
	if len(c.Sources) == 0 {
		return errors.New("at least one source is required")
	}
	seen := map[string]bool{}
	for i := range c.Sources {
		s := &c.Sources[i]
		if s.Name == "" {
			return fmt.Errorf("sources[%d]: name is empty", i)
		}
		if seen[s.Name] {
			return fmt.Errorf("sources[%d]: duplicate source name %q", i, s.Name)
		}
		seen[s.Name] = true
		if s.LI > 3 {
			return fmt.Errorf("source %s: li must be 0..3", s.Name)
		}
		if s.StepAt != "" {
			if _, err := time.Parse(time.RFC3339, s.StepAt); err != nil {
				return fmt.Errorf("source %s: step_at: %w", s.Name, err)
			}
		}
	}
	for _, l := range c.Links {
		if !seen[l.Source] {
			return fmt.Errorf("link for unknown source %q", l.Source)
		}
		if l.LossRate < 0 || l.LossRate > 1 {
			return fmt.Errorf("link %s: loss_rate must be in [0,1]", l.Source)
		}
		if l.DupRate < 0 || l.DupRate > 1 {
			return fmt.Errorf("link %s: dup_rate must be in [0,1]", l.Source)
		}
		if l.ToServer.Duration() < 0 || l.FromServer.Duration() < 0 {
			return fmt.Errorf("link %s: delays must be non-negative", l.Source)
		}
	}
	if c.Run.Rounds < 1 {
		return errors.New("run.rounds must be >= 1")
	}
	if c.Run.PollInterval.Duration() <= 0 {
		return errors.New("run.poll_interval must be > 0")
	}
	if c.Run.ReplyTimeout.Duration() <= 0 {
		return errors.New("run.reply_timeout must be > 0")
	}
	if c.Policy.MinSources < 1 {
		return errors.New("policy.min_sources must be >= 1")
	}
	if c.Policy.RTTOutlierFactor != 0 && c.Policy.RTTOutlierFactor < 1 {
		return errors.New("policy.rtt_outlier_factor must be >= 1 (or 0 to disable)")
	}
	if c.Run.Start == "" {
		return errors.New("run.start is required (RFC3339)")
	}
	if _, err := time.Parse(time.RFC3339, c.Run.Start); err != nil {
		return fmt.Errorf("run.start: %w", err)
	}
	return nil
}

// StartTime parses the run anchor.
func (c *Config) StartTime() time.Time {
	t, _ := time.Parse(time.RFC3339, c.Run.Start)
	return t
}

// Policy converts the config policy into a core.SelectionPolicy.
func (c *Config) PolicyOptions() core.SelectionPolicy {
	p := core.DefaultPolicy()
	if c.Policy.MaxSampleAge.Duration() != 0 {
		p.MaxSampleAge = c.Policy.MaxSampleAge.Duration()
	}
	if c.Policy.MaxRTT.Duration() != 0 {
		p.MaxRTT = c.Policy.MaxRTT.Duration()
	}
	if c.Policy.MaxClockJump.Duration() != 0 {
		p.MaxClockJump = c.Policy.MaxClockJump.Duration()
	}
	if c.Policy.RTTOutlierFactor > 0 {
		p.RTTOutlierFactor = c.Policy.RTTOutlierFactor
	}
	if c.Policy.MinSources > 0 {
		p.MinSources = c.Policy.MinSources
	}
	return p
}

// ServerProfile maps source config to the transport profile.
func (s SourceConfig) ServerProfile() transport.ServerProfile {
	return transport.ServerProfile{
		Stratum:     s.Stratum,
		LI:          s.LI,
		ReferenceID: s.ReferenceID,
		KissCode:    s.KissCode,
	}
}
