package transport

import (
	"context"
	"errors"
	"net"
	"sync"
	"time"

	"coaplab/internal/diag"
	"coaplab/internal/ids"
	"coaplab/internal/wire"
)

// Handler processes a request once per (endpoint, MID) first transmission.
type Handler interface {
	ServeCoAP(remote *net.UDPAddr, req *wire.Message) *wire.Message
}

// HandlerFunc adapts a function.
type HandlerFunc func(remote *net.UDPAddr, req *wire.Message) *wire.Message

// ServeCoAP implements Handler.
func (f HandlerFunc) ServeCoAP(remote *net.UDPAddr, req *wire.Message) *wire.Message {
	return f(remote, req)
}

// Server is a UDP CoAP server with message-layer MID deduplication.
// Responses are piggybacked (handler work is local/fast in this subset).
type Server struct {
	conn    *net.UDPConn
	handler Handler
	dedup   *DedupCache
	rec     *diag.Recorder

	mu      sync.Mutex
	closing bool
}

// ServerOptions configures a Server.
type ServerOptions struct {
	Addr     string // bind address, e.g. "127.0.0.1:5683"
	Handler  Handler
	DedupTTL time.Duration
	Recorder *diag.Recorder
}

// Listen binds the UDP socket.
func Listen(opts ServerOptions) (*Server, error) {
	addr, err := net.ResolveUDPAddr("udp", opts.Addr)
	if err != nil {
		return nil, err
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return nil, err
	}
	ttl := opts.DedupTTL
	if ttl <= 0 {
		ttl = 247 * time.Second
	}
	return &Server{
		conn:    conn,
		handler: opts.Handler,
		dedup:   NewDedupCache(ttl),
		rec:     opts.Recorder,
	}, nil
}

// Addr reports the bound address.
func (s *Server) Addr() *net.UDPAddr { return s.conn.LocalAddr().(*net.UDPAddr) }

// Serve runs the receive loop until Close or ctx cancellation.
func (s *Server) Serve(ctx context.Context) error {
	go func() {
		<-ctx.Done()
		_ = s.conn.SetReadDeadline(time.Now())
	}()
	buf := make([]byte, 65535)
	for {
		n, raddr, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			s.mu.Lock()
			closing := s.closing
			s.mu.Unlock()
			if closing || errors.Is(err, net.ErrClosed) || ctx.Err() != nil {
				return nil
			}
			return err
		}
		dgram := append([]byte(nil), buf[:n]...)
		go s.datagram(raddr, dgram)
	}
}

// Close stops listening.
func (s *Server) Close() error {
	s.mu.Lock()
	s.closing = true
	s.mu.Unlock()
	return s.conn.Close()
}

func (s *Server) datagram(raddr *net.UDPAddr, dgram []byte) {
	msg, perr := wire.Parse(dgram)
	if perr != nil {
		// A malformed datagram gives no trustworthy type/MID; RFC 7252 §4.2
		// permits silently dropping it. Diagnose and send nothing.
		s.log(raddr.String(), peekMID(dgram), len(dgram) >= 4, nil, diag.Reject, diag.CatParseError,
			"dropping %d-byte datagram: %s", len(dgram), perr)
		return
	}

	if msg.Type == wire.ACK || msg.Type == wire.RST {
		// Server-initiated exchanges are outside this subset; ignore.
		s.log(raddr.String(), msg.MID, true, msg.Token, diag.Ignore, diag.CatMessageLayer,
			"unexpected %s at server", msg.Type)
		return
	}

	if msg.Type == wire.NON {
		// NON messages are not deduplicated at the message layer (RFC 7252
		// §4.2 dedup is specified for Confirmable messages).
		s.dispatch(raddr, msg, false)
		return
	}

	dec := s.dedup.Observe(raddr.String(), ids.MID(msg.MID), dgram)
	if dec.Duplicate {
		if dec.Replay != nil {
			s.log(raddr.String(), msg.MID, true, msg.Token, diag.Ignore, "",
				"CON retransmission: replaying stored response, handler NOT re-invoked")
			_, _ = s.conn.WriteToUDP(dec.Replay, raddr)
			return
		}
		// First transmission ended without a stored response (e.g. a RST
		// path); nothing to replay.
		s.log(raddr.String(), msg.MID, true, msg.Token, diag.Indeterminate, "",
			"duplicate CON but first transmission has no stored response")
		return
	}

	s.dispatch(raddr, msg, true)
}

func (s *Server) dispatch(raddr *net.UDPAddr, req *wire.Message, confirmable bool) {
	resp := s.handler.ServeCoAP(raddr, req)
	if resp == nil {
		// Handler declined to answer; for CON emit RST so the client stops.
		if confirmable {
			raw, _ := wire.EmptyRST(req.MID).Marshal()
			_, _ = s.conn.WriteToUDP(raw, raddr)
			s.dedup.Fail(raddr.String(), ids.MID(req.MID))
		}
		return
	}
	if confirmable {
		resp.Type = wire.ACK
	} else {
		resp.Type = wire.NON
	}
	resp.MID = req.MID
	// A response MUST echo the request token (RFC 7252 §5.3.1).
	resp.Token = append(resp.Token[:0:0], req.Token...)
	raw, err := resp.Marshal()
	if err != nil {
		s.log(raddr.String(), req.MID, true, req.Token, diag.Reject, diag.CatParseError,
			"cannot marshal response: %s", err)
		if confirmable {
			s.dedup.Fail(raddr.String(), ids.MID(req.MID))
		}
		return
	}
	_, _ = s.conn.WriteToUDP(raw, raddr)
	if confirmable {
		s.dedup.StoreResponse(raddr.String(), ids.MID(req.MID), raw)
	}
}

func peekMID(b []byte) uint16 {
	if len(b) < 4 {
		return 0
	}
	return uint16(b[2])<<8 | uint16(b[3])
}

func (s *Server) log(remote string, mid uint16, haveMID bool, token []byte, v diag.Verdict, cat diag.Category, format string, args ...any) {
	if s.rec == nil {
		return
	}
	s.rec.Log(remote, mid, haveMID, ids.Token(token).Hex(), v, cat, format, args...)
}
