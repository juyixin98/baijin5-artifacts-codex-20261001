package protocol

import (
	"sync"
	"time"
)

// Clock abstracts time so retransmission can be driven deterministically in
// tests while using wall time in production.
type Clock interface {
	Now() time.Time
	// After fires after at least d. The returned stop cancels the timer.
	After(d time.Duration, fn func()) (stop func())
}

// RealClock uses the runtime timer.
type RealClock struct{}

// NewRealClock returns the wall-clock Clock.
func NewRealClock() Clock { return RealClock{} }

// Now returns the current time.
func (RealClock) Now() time.Time { return time.Now() }

// After schedules fn on a real time.Timer.
func (RealClock) After(d time.Duration, fn func()) func() {
	t := time.AfterFunc(d, fn)
	return func() { t.Stop() }
}

// FakeClock is a deterministic manual clock. Timers only fire when Advance
// moves time past their deadline. It is safe for concurrent use; the callback
// functions are invoked synchronously from Advance.
type FakeClock struct {
	mu      sync.Mutex
	now     time.Time
	nextID  int64
	timers  map[int64]fakeTimer
	stopped map[int64]bool
}

type fakeTimer struct {
	deadline time.Time
	fn       func()
}

// NewFakeClock returns a FakeClock set to t.
func NewFakeClock(t time.Time) *FakeClock {
	return &FakeClock{
		now:     t,
		timers:  make(map[int64]fakeTimer),
		stopped: make(map[int64]bool),
	}
}

// Now returns the fake current time.
func (c *FakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

// After registers fn at now+d on the fake timeline.
func (c *FakeClock) After(d time.Duration, fn func()) func() {
	c.mu.Lock()
	id := c.nextID
	c.nextID++
	c.timers[id] = fakeTimer{deadline: c.now.Add(d), fn: fn}
	c.mu.Unlock()
	return func() {
		c.mu.Lock()
		c.stopped[id] = true
		c.mu.Unlock()
	}
}

// Advance moves time forward by d, firing due timers in deadline order. The
// clock walks to each timer's actual deadline, so a timer scheduled during an
// earlier callback within the same window is itself eligible to fire.
func (c *FakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	target := c.now.Add(d)
	defer c.mu.Unlock()
	for {
		var earliestID int64 = -1
		var earliest time.Time
		for id, t := range c.timers {
			if c.stopped[id] || t.deadline.After(target) {
				continue
			}
			if earliestID == -1 || t.deadline.Before(earliest) {
				earliestID, earliest = id, t.deadline
			}
		}
		if earliestID == -1 {
			c.now = target
			return
		}
		c.now = earliest
		c.stopped[earliestID] = true // fire-once semantics
		fn := c.timers[earliestID].fn
		c.mu.Unlock()
		fn()
		c.mu.Lock()
	}
}

// PendingTimers reports how many registered timers have not fired/stopped.
func (c *FakeClock) PendingTimers() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	n := 0
	for id := range c.timers {
		if !c.stopped[id] {
			n++
		}
	}
	return n
}
