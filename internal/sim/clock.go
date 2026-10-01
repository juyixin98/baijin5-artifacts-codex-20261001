package sim

import (
	"sync"
	"time"
)

// SimClock is a source's local clock: virtual reference time plus a constant
// offset, a linear drift and discrete steps. It implements clock.Clock and is
// read by the inline NTP server handler. It never changes the system clock.
type SimClock struct {
	mu  sync.Mutex
	env *Environment

	offset time.Duration // fixed offset from reference/true time
	// drift is seconds-per-second; applied to elapsed time since anchor.
	anchor   time.Time
	driftPPM int64 // microseconds per second drift (ppm)
	step     time.Duration
	stepAt   time.Time
	hasStep  bool
}

// NewSimClock builds a source clock reading env time with a fixed offset.
func NewSimClock(env *Environment, anchor time.Time, offset time.Duration) *SimClock {
	return &SimClock{env: env, offset: offset, anchor: anchor}
}

// SetDrift installs a constant linear drift in parts-per-million (us/s).
func (c *SimClock) SetDrift(ppm int64) *SimClock {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.driftPPM = ppm
	return c
}

// StepAt schedules (or applies, if at/before now) a one-time jump of d at t.
func (c *SimClock) StepAt(t time.Time, d time.Duration) *SimClock {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.stepAt = t
	c.step = d
	c.hasStep = true
	return c
}

// Offset returns the configured static offset.
func (c *SimClock) Offset() time.Duration {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.offset
}

// Now returns what this source believes the time to be.
func (c *SimClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	ref := c.env.Now()
	elapsedNs := ref.Sub(c.anchor).Nanoseconds()
	// ppm (us/s) => ns of drift = elapsed_ns * ppm / 1e6.
	drift := time.Duration(elapsedNs * c.driftPPM / 1_000_000)
	t := ref.Add(c.offset).Add(drift)
	if c.hasStep && !ref.Before(c.stepAt) {
		t = t.Add(c.step)
	}
	return t
}
