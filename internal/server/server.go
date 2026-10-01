// Package server implements a controlled local STUN Binding server over UDP.
//
// Scope (RFC 5389 subset):
//   - answers Binding Requests with an XOR-MAPPED-ADDRESS of the datagram
//     source (plus SOFTWARE);
//   - optionally attaches MESSAGE-INTEGRITY (HMAC-SHA1 over a pre-shared key)
//     and FINGERPRINT to responses, and verifies an inbound integrity attr;
//   - answers 420 with UNKNOWN-ATTRIBUTES for unknown comprehension-required
//     attributes;
//   - TURN relay/allocations/channels are deliberately not implemented.
package server

import (
	"errors"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"localstun/internal/audit"
	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

// Config configures Server.
type Config struct {
	// ListenAddr binds the UDP socket, e.g. "127.0.0.1:0" or "[::1]:0".
	ListenAddr string
	// SharedKey enables verification of request integrity and signing of
	// responses with a symmetric pre-shared HMAC key (controlled test only).
	SharedKey []byte
	// Fingerprint adds FINGERPRINT to responses.
	Fingerprint bool
	// Realm labels error responses. Default "localstun.test".
	Realm string
	// MaxPacketLen rejects datagrams larger than this (default 2048).
	MaxPacketLen int
	// Sink receives audit records; nil disables persistence.
	Sink audit.Sink
	// Now is injectable for deterministic tests.
	Now func() time.Time
	// RunID tags audit records so a run can be replayed; default generated.
	RunID string
}

// Server is a UDP STUN Binding server.
type Server struct {
	cfg  Config
	conn *net.UDPConn
	wg   sync.WaitGroup

	closeMu sync.Mutex
	closed  bool
	seq     atomic.Uint64
}

// New validates config (it does not bind the socket; call Serve).
func New(cfg Config) (*Server, error) {
	if cfg.ListenAddr == "" {
		return nil, stunerror.New(stunerror.KindInput, "server.new", "ListenAddr is required")
	}
	if cfg.Fingerprint && cfg.SharedKey == nil {
		return nil, stunerror.New(stunerror.KindInput, "server.new", "FINGERPRINT requires a shared key")
	}
	if cfg.MaxPacketLen <= 0 {
		cfg.MaxPacketLen = 2048
	}
	if cfg.Realm == "" {
		cfg.Realm = "localstun.test"
	}
	if cfg.Sink == nil {
		cfg.Sink = audit.Noop
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.RunID == "" {
		cfg.RunID = "run-" + strconvFormatTime(cfg.Now())
	}
	return &Server{cfg: cfg}, nil
}

// Addr returns the bound address once Serve has started; nil beforehand.
func (s *Server) Addr() *net.UDPAddr {
	if s.conn == nil {
		return nil
	}
	return s.conn.LocalAddr().(*net.UDPAddr)
}

// RunID returns the audit run tag.
func (s *Server) RunID() string { return s.cfg.RunID }

// Serve binds and serves until Close or a fatal socket error. It blocks; run
// it in a goroutine. The returned error channel receives the terminal error.
func (s *Server) Serve() <-chan error {
	errc := make(chan error, 1)
	addr, err := net.ResolveUDPAddr("udp", s.cfg.ListenAddr)
	if err != nil {
		errc <- stunerror.Wrap(stunerror.KindInput, "server.serve", "resolve listen address", err)
		close(errc)
		return errc
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		errc <- stunerror.Wrap(stunerror.KindCompute, "server.serve", "listen failed", err)
		close(errc)
		return errc
	}
	s.conn = conn
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		errc <- s.loop()
		close(errc)
	}()
	return errc
}

func (s *Server) loop() error {
	buf := make([]byte, s.cfg.MaxPacketLen)
	for {
		n, src, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			s.closeMu.Lock()
			closed := s.closed
			s.closeMu.Unlock()
			if closed || errors.Is(err, net.ErrClosed) {
				return nil
			}
			return stunerror.Wrap(stunerror.KindCompute, "server.loop", "socket read failed", err)
		}
		pkt := make([]byte, n)
		copy(pkt, buf[:n])
		// Every datagram is handled independently; UDP is stateless.
		go s.handle(pkt, src)
	}
}

// Handle processes one datagram and returns the response bytes (nil when the
// datagram is silently dropped). Exported through the unexported loop only;
// kept as a method so the audit record and response share one code path in
// tests via HandlePacket.
func (s *Server) handle(pkt []byte, src *net.UDPAddr) []byte {
	rec := s.newRecord("datagram_received", src)
	rec.WireHex = hexEncode(pkt)
	// Transaction id lives at bytes 8..19 whenever the datagram has a header;
	// copying it lets audit rows correlate requests with their responses.
	if len(pkt) >= stun.HeaderLen {
		rec.TxID = hexEncode(pkt[8 : 8+12])
	}

	resp, kind, event, detail := s.HandlePacket(pkt, src)

	rec.Event = event
	rec.Kind = kindLabel(kind)
	rec.Detail = detail
	if resp != nil {
		if _, err := s.conn.WriteToUDP(resp, src); err != nil {
			rec.Event = "response_send_failed"
			rec.Kind = stunerror.KindCompute.String()
			rec.Detail = err.Error()
		} else {
			rec.WireHex = rec.WireHex + " -> " + hexEncode(resp)
		}
	}
	s.writeAudit(rec)
	return resp
}

// HandlePacket is the pure protocol decision: given request bytes and a
// source address, return the response bytes (nil to drop) and outcome
// metadata. It performs no I/O so it is directly unit-testable.
func (s *Server) HandlePacket(pkt []byte, src *net.UDPAddr) (resp []byte, kind stunerror.Kind, event, detail string) {
	if len(pkt) > s.cfg.MaxPacketLen {
		return nil, stunerror.KindExhausted, "datagram_too_large",
			"datagram exceeds configured maximum"
	}

	msg, err := stun.Decode(pkt, s.cfg.SharedKey)
	if err != nil {
		return s.decodeErrorResponse(pkt, err)
	}

	if msg.Type != stun.BindingRequest {
		return nil, stunerror.KindInput, "not_binding_request",
			"only Binding method is supported"
	}

	// Integrity required when the server holds a key and the request claims
	// one; an unsigned request to a signing server is a 401 in this test
	// harness (so tampering/forgery is observable end-to-end).
	if s.cfg.SharedKey != nil && !msg.IntegrityOK {
		errMsg := s.errorResponse(msg.TxID, stun.StatusUnauthorized, "Unauthorized")
		out, merr := stun.Marshal(errMsg, s.cfg.SharedKey, s.cfg.Fingerprint)
		if merr != nil {
			return nil, stunerror.KindCompute, "response_marshal_failed", merr.Error()
		}
		return out, stunerror.KindIntegrity, "unauthorized_request",
			"request lacked valid MESSAGE-INTEGRITY"
	}

	out, err := s.buildSuccess(msg, src)
	if err != nil {
		return nil, stunerror.KindCompute, "response_build_failed", err.Error()
	}
	return out, stunerror.KindUnknown, "binding_success",
		"mapped " + src.String()
}

func (s *Server) buildSuccess(msg *stun.Message, src *net.UDPAddr) ([]byte, error) {
	resp := stun.NewMessage(stun.BindingResponse, msg.TxID)
	if err := resp.AddXORMappedAddress(src.IP, src.Port); err != nil {
		return nil, err
	}
	resp.AddSoftware()
	return stun.Marshal(resp, s.cfg.SharedKey, s.cfg.Fingerprint)
}

// decodeErrorResponse turns a decode failure into the correct wire behavior:
// 420 for unknown comprehension-required attrs, drop for structural junk.
func (s *Server) decodeErrorResponse(pkt []byte, err error) ([]byte, stunerror.Kind, string, string) {
	var unknown *stun.UnknownRequiredError
	if errors.As(err, &unknown) {
		resp := stun.NewMessage(stun.BindingError, unknown.TxID)
		types := make([]byte, 0, 2*len(unknown.Unknown))
		for _, t := range unknown.Unknown {
			types = appendUint16(types, uint16(t))
		}
		resp.Add(stun.AttrUnknownAttrs, types)
		resp.AddErrorCode(stun.StatusUnknownAttribute, "Unknown Attribute")
		out, merr := stun.Marshal(resp, s.cfg.SharedKey, s.cfg.Fingerprint)
		if merr != nil {
			return nil, stunerror.KindCompute, "response_marshal_failed", merr.Error()
		}
		return out, stunerror.KindIntegrity, "unknown_required_attribute",
			"rejected with 420"
	}
	kind := stunerror.Of(err)
	if kind == stunerror.KindUnknown {
		kind = stunerror.KindInput
	}
	return nil, kind, "decode_failed", err.Error()
}

func (s *Server) errorResponse(txID stun.TransactionID, code int, reason string) *stun.Message {
	m := stun.NewMessage(stun.BindingError, txID)
	m.AddErrorCode(code, reason)
	m.Add(stun.AttrRealm, []byte(s.cfg.Realm))
	m.AddSoftware()
	return m
}

// Close stops the server.
func (s *Server) Close() error {
	s.closeMu.Lock()
	s.closed = true
	s.closeMu.Unlock()
	var err error
	if s.conn != nil {
		err = s.conn.Close()
	}
	s.wg.Wait()
	return err
}

func (s *Server) newRecord(event string, src *net.UDPAddr) audit.Record {
	n := s.seq.Add(1)
	r := audit.Record{
		RunID:     s.cfg.RunID,
		Seq:       n,
		Timestamp: s.cfg.Now(),
		Component: "server",
		Event:     event,
		SrcAddr:   src.String(),
		DstAddr:   s.cfg.ListenAddr,
	}
	return r
}

func (s *Server) writeAudit(r audit.Record) {
	_ = s.cfg.Sink.Write(r) // sink failures must not take the dataplane down
}

func kindLabel(k stunerror.Kind) string {
	if k == stunerror.KindUnknown {
		return ""
	}
	return k.String()
}

func appendUint16(b []byte, v uint16) []byte {
	return append(b, byte(v>>8), byte(v))
}

func strconvFormatTime(t time.Time) string {
	return t.UTC().Format("20060102T150405.000000000Z")
}

func hexEncode(b []byte) string {
	const hexd = "0123456789abcdef"
	out := make([]byte, len(b)*2)
	for i, c := range b {
		out[2*i] = hexd[c>>4]
		out[2*i+1] = hexd[c&0x0F]
	}
	return string(out)
}
