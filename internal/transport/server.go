package transport

import (
	"fmt"
	"time"

	"ntpsim/internal/clock"
	"ntpsim/internal/protocol"
)

// ServerProfile configures the controlled NTP server.
type ServerProfile struct {
	// Stratum the server advertises (0 => kiss-o'-death replies).
	Stratum uint8
	// LI leap indicator (3 = alarm / unsynchronized).
	LI uint8
	// ReferenceID advertised in replies (e.g. 0x0A000001).
	ReferenceID uint32
	// KissCode is emitted as ReferenceID when Stratum == 0 ("DENY", "RATE", ...).
	KissCode string
	// RootDelay / RootDispersion widen client uncertainty.
	RootDelaySecs uint16
	RootDelayFrac uint16
	RootDispSecs  uint16
	RootDispFrac  uint16
	// ProcessingGap is the server-internal hold time between receive (t2) and
	// transmit (t3). The caller advances its clock externally; zero means the
	// server stamps both from the same Now() call.
	ProcessingGapNanos int64
}

// DefaultServerProfile is a healthy stratum-2 server.
func DefaultServerProfile() ServerProfile {
	return ServerProfile{Stratum: 2, LI: protocol.LINoWarning, ReferenceID: 0x0A000001}
}

// Server is the receive-request -> send-reply state machine. It holds no
// goroutine of its own: ServeOnce is driven by a loop in run/harness.
type Server struct {
	clk  clock.Clock
	prof ServerProfile
}

// NewServer builds a server state machine over clk with the given profile.
func NewServer(clk clock.Clock, prof ServerProfile) *Server {
	return &Server{clk: clk, prof: prof}
}

// BuildReply constructs the server response wire bytes for one request.
// Pure function of (request bytes, server clock): easy to unit test.
func (s *Server) BuildReply(req []byte) ([]byte, error) {
	p, err := protocol.DecodeLax(req)
	if err != nil {
		return nil, fmt.Errorf("server: cannot parse request: %w", err)
	}
	if p.Mode != protocol.ModeClient {
		return nil, fmt.Errorf("server: not a client request (mode=%d)", p.Mode)
	}

	t2 := s.clk.Now()
	t3 := t2
	if s.prof.ProcessingGapNanos != 0 {
		t3 = t2.Add(time.Duration(s.prof.ProcessingGapNanos))
	}

	reply := &protocol.Packet{
		LI:             s.prof.LI,
		Version:        p.Version,
		Mode:           protocol.ModeServer,
		Stratum:        s.prof.Stratum,
		Poll:           p.Poll,
		Precision:      -20,
		RootDelay:      protocol.NTPShort{Seconds: s.prof.RootDelaySecs, Fraction: s.prof.RootDelayFrac},
		RootDispersion: protocol.NTPShort{Seconds: s.prof.RootDispSecs, Fraction: s.prof.RootDispFrac},
		OriginTime:     p.TransmitTime, // MUST echo client's transmit timestamp
		ReceiveTime:    protocol.TimestampFromTime(t2),
		TransmitTime:   protocol.TimestampFromTime(t3),
	}
	if s.prof.Stratum == 0 && s.prof.KissCode != "" {
		reply.ReferenceID = encodeKiss(s.prof.KissCode)
	} else {
		reply.ReferenceID = s.prof.ReferenceID
	}
	return reply.Encode(), nil
}

// ServeOnce reads exactly one request from dg and sends the reply back to the
// sender. Returns the number of request bytes and any transport error.
func (s *Server) ServeOnce(dg Datagram) (int, error) {
	b, from, err := dg.Recv(noDeadline())
	if err != nil {
		return 0, err
	}
	out, err := s.BuildReply(b)
	if err != nil {
		// Protocol errors get no reply (real NTP servers drop junk).
		return len(b), nil
	}
	if err := dg.Send(out, from); err != nil {
		return len(b), err
	}
	return len(b), nil
}

func encodeKiss(code string) uint32 {
	if len(code) != 4 {
		return 0
	}
	return uint32(code[0])<<24 | uint32(code[1])<<16 | uint32(code[2])<<8 | uint32(code[3])
}

// noDeadline returns the zero Time, which net.Conn treats as "no deadline"
// and the simulated fabric interprets as "block until a packet exists".
func noDeadline() time.Time { return time.Time{} }
