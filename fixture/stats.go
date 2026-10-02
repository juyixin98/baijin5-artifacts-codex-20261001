package fixture

import "sync/atomic"

// stats are cheap process counters surfaced on the control plane and in
// logs. All fields are updated atomically; no request blocks on stats.
type stats struct {
	connectionsOpened atomic.Int64
	connectionsClosed atomic.Int64
	requestsOK        atomic.Int64
	exceptions        [256]atomic.Int64
	framingErrors     atomic.Int64
}

func newStats() *stats { return &stats{} }

func (s *stats) connOpened()   { s.connectionsOpened.Add(1) }
func (s *stats) connClosed()   { s.connectionsClosed.Add(1) }
func (s *stats) success()      { s.requestsOK.Add(1) }
func (s *stats) framingError() { s.framingErrors.Add(1) }
func (s *stats) exception(c byte) {
	if c == 0 {
		return
	}
	s.exceptions[c].Add(1)
}

// Snapshot is a consistent-enough point-in-time view for the control API.
type StatsSnapshot struct {
	ConnectionsOpened int64            `json:"connections_opened"`
	ConnectionsClosed int64            `json:"connections_closed"`
	RequestsOK        int64            `json:"requests_ok"`
	FramingErrors     int64            `json:"framing_errors"`
	Exceptions        map[string]int64 `json:"exceptions"`
}

func (s *stats) snapshot() StatsSnapshot {
	snap := StatsSnapshot{
		ConnectionsOpened: s.connectionsOpened.Load(),
		ConnectionsClosed: s.connectionsClosed.Load(),
		RequestsOK:        s.requestsOK.Load(),
		FramingErrors:     s.framingErrors.Load(),
		Exceptions:        make(map[string]int64),
	}
	for c := 1; c < 256; c++ {
		if v := s.exceptions[c].Load(); v > 0 {
			snap.Exceptions[byteHex(byte(c))] = v
		}
	}
	return snap
}

func byteHex(b byte) string {
	const digits = "0123456789ABCDEF"
	return "0x" + string([]byte{digits[b>>4], digits[b&0xF]})
}
