package clock_test

import (
	"testing"
	"time"

	"ntpsim/internal/clock"
)

func TestFakeClockAdvanceAndSet(t *testing.T) {
	start := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)
	c := clock.NewFakeClock(start)
	if c.Now() != start {
		t.Fatalf("now=%v want %v", c.Now(), start)
	}
	c.Advance(150 * time.Millisecond)
	if got := c.Now(); !got.Equal(start.Add(150 * time.Millisecond)) {
		t.Fatalf("after advance=%v", got)
	}
	target := time.Date(2030, 6, 1, 0, 0, 0, 0, time.UTC)
	c.Set(target)
	if c.Now() != target {
		t.Fatalf("after set=%v want %v", c.Now(), target)
	}
}

func TestWallClockReadOnly(t *testing.T) {
	// WallClock only reads time; two reads must be monotonic at nanosecond scale
	// tolerance (this also documents the no-set guarantee).
	w := clock.WallClock{}
	a := w.Now()
	b := w.Now()
	if b.Before(a) {
		t.Fatalf("wall clock moved backwards: %v -> %v", a, b)
	}
}
