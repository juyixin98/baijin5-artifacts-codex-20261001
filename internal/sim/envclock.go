package sim

import "time"

// EnvClock adapts Environment to clock.Clock. The NTP client uses it as its
// local clock: by definition the client clock is the reference, and each
// source's SimClock offset is what the exchange math estimates.
type EnvClock struct{ env *Environment }

// NewEnvClock wraps env as a clock.Clock.
func NewEnvClock(env *Environment) EnvClock { return EnvClock{env: env} }

// Now returns the current virtual reference time.
func (c EnvClock) Now() time.Time { return c.env.Now() }
