package harness

import "time"

// fixedPast is an instant long before any test run, used as the bogus origin
// timestamp replayed by ReplayUDPServer.
func fixedPast() time.Time {
	return time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC)
}
