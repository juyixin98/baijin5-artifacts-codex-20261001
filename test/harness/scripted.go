package harness

import (
	"net"
	"sync"
	"time"

	"coaplab/internal/wire"
)

// ScriptedServer is a raw UDP responder driven by a callback. It lets tests
// craft exact bytes in replies (wrong token, wrong MID, silence, ...) to
// verify the client's message/request layer separation.
type ScriptedServer struct {
	conn    *net.UDPConn
	mu      sync.Mutex
	started bool
	stop    chan struct{}
	seen    []SeenDatagram
	onMsg   func(msg *wire.Message, raw []byte, from *net.UDPAddr) *wire.Message
}

// SeenDatagram records one inbound datagram at the scripted server.
type SeenDatagram struct {
	Msg *wire.Message
	Raw []byte
}

// NewScriptedServer binds an ephemeral UDP socket.
func NewScriptedServer() (*ScriptedServer, error) {
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		return nil, err
	}
	return &ScriptedServer{conn: conn, stop: make(chan struct{})}, nil
}

// Addr is the bind address clients send to.
func (s *ScriptedServer) Addr() *net.UDPAddr { return s.conn.LocalAddr().(*net.UDPAddr) }

// OnMessage installs the reply constructor; returning nil means stay silent.
func (s *ScriptedServer) OnMessage(f func(msg *wire.Message, raw []byte, from *net.UDPAddr) *wire.Message) {
	s.mu.Lock()
	s.onMsg = f
	s.mu.Unlock()
}

// Start begins receiving.
func (s *ScriptedServer) Start() {
	s.mu.Lock()
	if s.started {
		s.mu.Unlock()
		return
	}
	s.started = true
	s.mu.Unlock()
	go s.loop()
}

// Seen returns all datagrams received so far.
func (s *ScriptedServer) Seen() []SeenDatagram {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]SeenDatagram, len(s.seen))
	copy(out, s.seen)
	return out
}

// Close stops the server.
func (s *ScriptedServer) Close() {
	select {
	case <-s.stop:
	default:
		close(s.stop)
	}
	_ = s.conn.Close()
}

func (s *ScriptedServer) loop() {
	buf := make([]byte, 65535)
	for {
		_ = s.conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		n, from, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-s.stop:
				return
			default:
				continue
			}
		}
		raw := append([]byte(nil), buf[:n]...)
		msg, _ := wire.Parse(raw)
		s.mu.Lock()
		s.seen = append(s.seen, SeenDatagram{Msg: msg, Raw: raw})
		f := s.onMsg
		s.mu.Unlock()
		if f == nil {
			continue
		}
		reply := f(msg, raw, from)
		if reply != nil {
			out, err := reply.Marshal()
			if err == nil {
				_, _ = s.conn.WriteToUDP(out, from)
			}
		}
	}
}
