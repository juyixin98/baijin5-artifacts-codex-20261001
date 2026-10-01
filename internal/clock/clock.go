// Package clock defines the time-source abstraction.
//
// All code in this project reads "now" through a Clock so that tests run on a
// fully deterministic virtual clock. The real executable injects WallClock;
// simulations and tests inject FakeClock. The system clock is NEVER adjusted:
// WallClock.Now only reads it.
package clock

import "time"

// Clock is the minimal time interface used across the project.
type Clock interface {
	// Now returns the current instant.
	Now() time.Time
}

// WallClock reads the operating-system clock without modifying it.
type WallClock struct{}

// Now implements Clock.
func (WallClock) Now() time.Time { return time.Now() }
