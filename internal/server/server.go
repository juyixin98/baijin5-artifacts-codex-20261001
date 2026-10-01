// Package server hosts the controlled SOCKS5 service: the accept loop, the
// per-connection bound, handshake orchestration, vetted-address dialing and
// the post-handshake relay.
package server

import (
	"context"
	"fmt"
	"net"
	"net/netip"
	"sync"
	"sync/atomic"
	"time"

	"socks5d.local/socks5d/internal/config"
	"socks5d.local/socks5d/internal/logx"
	"socks5d.local/socks5d/internal/policy"
	"socks5d.local/socks5d/internal/proto"
	"socks5d.local/socks5d/internal/relay"
	"socks5d.local/socks5d/internal/store"
)

// AppVersion is reported in every log line.
const AppVersion = "socks5d/1.0.0"

// vettedDialer dials exactly the netip.AddrPort the policy approved. It never
// takes a hostname, so there is no second resolution and no check/use gap.
type vettedDialer struct {
	d *net.Dialer
}

func (v vettedDialer) DialContext(ctx context.Context, target netip.AddrPort) (net.Conn, error) {
	return v.d.DialContext(ctx, "tcp", target.String())
}

// Server is the running proxy.
type Server struct {
	cfg      config.Config
	pol      *policy.Policy
	resolver policy.Resolver
	authn    proto.Authenticator
	store    *store.Store
	log      *logx.Logger

	listener net.Listener
	sem      chan struct{} // bounded connection count

	mu      sync.Mutex
	closed  bool
	wg      sync.WaitGroup
	closeCh chan struct{}
}

// New wires a server from validated configuration.
func New(cfg config.Config, pol *policy.Policy, resolver policy.Resolver,
	authn proto.Authenticator, st *store.Store, log *logx.Logger) *Server {
	return &Server{
		cfg:      cfg,
		pol:      pol,
		resolver: resolver,
		authn:    authn,
		store:    st,
		log:      log,
		sem:      make(chan struct{}, cfg.MaxConnections),
		closeCh:  make(chan struct{}),
	}
}

// Listen binds the configured address.
func (s *Server) Listen() error {
	ln, err := net.Listen("tcp", s.cfg.Listen)
	if err != nil {
		return fmt.Errorf("listen %s: %w", s.cfg.Listen, err)
	}
	s.listener = ln
	return nil
}

// Addr reports the bound address (useful when listen port is 0 in tests).
func (s *Server) Addr() net.Addr { return s.listener.Addr() }

// Serve accepts and handles connections until the listener closes.
func (s *Server) Serve(ctx context.Context) error {
	s.log.Root().Info("serve_start", "accept_loop")
	for {
		conn, err := s.listener.Accept()
		if err != nil {
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed {
				s.log.Root().Info("serve_stop", "accept_loop")
				return nil
			}
			return fmt.Errorf("accept: %w", err)
		}
		select {
		case s.sem <- struct{}{}:
		default:
			// At capacity: refuse explicitly rather than queue unbounded.
			s.log.Root().Fail("accept_at_capacity", "accept_loop", "max_connections",
				fmt.Sprintf("limit %d reached", s.cfg.MaxConnections))
			_ = conn.Close()
			continue
		}
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			defer func() { <-s.sem }()
			s.handle(ctx, conn)
		}()
	}
}

// Shutdown stops accepting and waits for in-flight connections up to timeout.
func (s *Server) Shutdown(timeout time.Duration) {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	s.mu.Unlock()
	close(s.closeCh)
	if s.listener != nil {
		_ = s.listener.Close()
	}
	done := make(chan struct{})
	go func() {
		s.wg.Wait()
		close(done)
	}()
	if timeout <= 0 {
		<-done
		return
	}
	select {
	case <-done:
	case <-time.After(timeout):
		s.log.Root().Fail("shutdown_timeout", "accept_loop", "grace_period_exceeded",
			fmt.Sprintf("some connections still active after %s", timeout))
	}
}

func (s *Server) handle(baseCtx context.Context, conn net.Conn) {
	client := conn.RemoteAddr().String()
	reqID := newRequestID(client)
	rl := s.log.Sub(reqID, client, "")
	rl.Info("connection_open", "accept")

	defer func() {
		_ = conn.Close()
		rl.Info("connection_close", "teardown")
	}()

	// Bound the whole handshake so a stalled peer cannot hold a slot forever.
	hsCtx, cancel := context.WithTimeout(baseCtx, s.cfg.HandshakeTimeout.Duration+
		s.cfg.DialTimeout.Duration+5*time.Second)
	defer cancel()

	hopts := proto.Options{
		Auth:     s.authn,
		Policy:   s.pol,
		Resolver: s.resolver,
		Dialer:   vettedDialer{d: &net.Dialer{Timeout: s.cfg.DialTimeout.Duration}},
		Timeouts: proto.Timeouts{
			Method: s.cfg.HandshakeTimeout.Duration,
			Auth:   s.cfg.HandshakeTimeout.Duration,
			Req:    s.cfg.HandshakeTimeout.Duration,
			Dial:   s.cfg.DialTimeout.Duration,
		},
	}

	est, err := proto.Handshake(hsCtx, conn, hopts)
	if err != nil {
		s.recordHandshakeFailure(reqID, rl, conn, err)
		return
	}
	rlTarget := rl.WithTarget(est.Target.String())
	rlTarget.Info("connect_established", "relay",
		"matched", "vetted", "rep", fmt.Sprintf("0x%02x", est.Rep))
	s.audit(store.AuditRow{
		ReqID: reqID, Client: client, Target: est.Target.String(),
		Stage: "connect", Result: logx.ResultOK,
	})

	defer func() { _ = est.Upstream.Close() }()

	relayCtx, relayCancel := context.WithCancel(baseCtx)
	defer relayCancel()
	go func() {
		select {
		case <-s.closeCh:
			relayCancel()
		case <-relayCtx.Done():
		}
	}()

	stats, rerr := relay.Relay(relayCtx, conn, est.Upstream, relay.Options{
		ByteBudget:  s.cfg.ByteBudget,
		IdleTimeout: s.cfg.IdleTimeout.Duration,
	})
	if rerr != nil {
		rlTarget.Fail("relay_end", "relay", stats.EndReason, rerr.Error(),
			"up", stats.ClientToUpstream, "down", stats.UpstreamToClient)
	} else {
		rlTarget.Info("relay_end", "relay",
			"up", stats.ClientToUpstream, "down", stats.UpstreamToClient,
			"end", stats.EndReason)
	}
	result := logx.ResultOK
	reason := ""
	detail := ""
	if rerr != nil {
		result = logx.ResultFail
		if stats.EndReason == "both_directions_closed" {
			result = logx.ResultUncertain
		}
		reason = stats.EndReason
		detail = rerr.Error()
	}
	s.audit(store.AuditRow{
		ReqID: reqID, Client: client, Target: est.Target.String(),
		Stage: "relay", Result: result, Reason: reason, Detail: detail,
		BytesUp: stats.ClientToUpstream, BytesDown: stats.UpstreamToClient,
	})
}

func (s *Server) recordHandshakeFailure(reqID string, rl *logx.RequestLogger, conn net.Conn, err error) {
	te, ok := proto.IsTerminal(err)
	if !ok {
		rl.Fail("handshake_error", "handshake", "unknown", err.Error())
		s.audit(store.AuditRow{
			ReqID: reqID, Client: conn.RemoteAddr().String(), Stage: "handshake",
			Result: logx.ResultFail, Reason: "unknown", Detail: err.Error(),
		})
		return
	}
	result := logx.ResultFail
	detail := te.Detail
	// A clean EOF mid-handshake is an inconclusive client abandonment, not a
	// policy decision: record it separately as uncertain.
	if te.Outcome == proto.OutcomeProtocolError && (te.Reason == "client_eof" || te.Reason == "deadline_exceeded") {
		rl.Uncertain("handshake_incomplete", "handshake", te.Reason, detail,
			"outcome", string(te.Outcome), "reply_sent", te.ReplySent)
		result = logx.ResultUncertain
	} else {
		rl.Fail("handshake_rejected", "handshake", te.Reason, detail,
			"outcome", string(te.Outcome), "reply_sent", te.ReplySent,
			"rep", fmt.Sprintf("0x%02x", te.Rep))
	}
	s.audit(store.AuditRow{
		ReqID: reqID, Client: conn.RemoteAddr().String(), Stage: "handshake",
		Result: result, Reason: te.Reason, Detail: fmt.Sprintf("%s: %s", te.Outcome, detail),
	})
}

func (s *Server) audit(row store.AuditRow) {
	if s.store == nil {
		return
	}
	row.TS = time.Now().UTC().Format(time.RFC3339Nano)
	if row.ReqID == "" {
		row.ReqID = "-"
	}
	if err := s.store.InsertAudit(context.Background(), row); err != nil {
		s.log.Root().Fail("audit_write_failed", "audit", "sqlite", err.Error())
	}
}

// newRequestID builds a correlation id unique per connection for log/audit
// correlation without external state.
var idCounter atomic.Uint64

func newRequestID(client string) string {
	n := idCounter.Add(1)
	port := "0"
	if _, p, err := net.SplitHostPort(client); err == nil {
		port = p
	}
	return fmt.Sprintf("r-%d-c%s", n, port)
}
