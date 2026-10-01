package sim

import (
	"time"

	"ntpsim/internal/transport"
)

// SourceSpec defines one synthesized NTP source.
type SourceSpec struct {
	// Name is the fabric endpoint name, e.g. "src-a".
	Name string
	// ClockOffset is the source's fixed offset from true/reference time.
	ClockOffset time.Duration
	// DriftPPM applies linear drift (microseconds per second).
	DriftPPM int64
	// StepAt / StepAmount model a clock jump at a virtual instant.
	StepAt     time.Time
	StepAmount time.Duration
	// Profile controls the NTP server reply fields (stratum, LI, KoD, ...).
	Profile transport.ServerProfile
}

// RegisterSource installs one inline NTP server on env and returns its clock
// so callers may issue further steps later. anchor is the virtual time the
// drift is measured from.
func RegisterSource(env *Environment, anchor time.Time, spec SourceSpec) *SimClock {
	sc := NewSimClock(env, anchor, spec.ClockOffset)
	sc.SetDrift(spec.DriftPPM)
	if !spec.StepAt.IsZero() {
		sc.StepAt(spec.StepAt, spec.StepAmount)
	}
	if spec.Profile.Stratum == 0 && spec.Profile.KissCode == "" {
		// Default stratum-0 reply is a DENY kiss.
		spec.Profile.KissCode = "DENY"
	}
	srv := transport.NewServer(sc, spec.Profile)
	env.Serve(spec.Name, func(in Incoming) []Outgoing {
		out, err := srv.BuildReply(in.Data)
		if err != nil {
			return nil // drop malformed request, like a real server
		}
		return []Outgoing{{To: in.From, Data: out}}
	})
	return sc
}
