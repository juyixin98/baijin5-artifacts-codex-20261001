// Package server is the controlled service: it accepts TCP connections,
// bounds their number with a semaphore, assigns a correlation id to each,
// runs the SOCKS5 state machine, relays successful CONNECTs, and records a
// durable, explainable row per request in SQLite.
package server

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"sync"
	"sync/atomic"
	"time"

	"sockswhitelist/internal/config"
	"sockswhitelist/internal/gateway"
	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/proto"
	"sockswhitelist/internal/relay"
	"sockswhitelist/internal/wire"
)

// Server is the SOCKS5 proxy listener.
type Server struct {
	cfg   config.Config
	store *policy.Store
	log   *Logger
	mach  *proto.Machine
	ln    net.Listener

	sem      chan struct{}
	wg       sync.WaitGroup
	active   atomic.Int64
	shutdown chan struct{}
	closed   atomic.Bool

	// metrics
	totalConns    atomic.Int64
	rejectedConns atomic.Int64
}

// New wires the service. connector is injectable for tests; pass nil to use
// the production resolver/dialer against the policy store.
func New(cfg config.Config, store *policy.Store, connector proto.Connecter, log *Logger) (*Server, error) {
	if connector == nil {
		connector = gateway.NewConnector(store, cfg.Limits.ResolveTimeout, cfg.Limits.DialTimeout)
	}
	if log == nil {
		log = NewLogger(nil, cfg.Log.Level, cfg.Log.TextFormat)
	}
	mach := proto.NewMachine(store, connector, proto.Options{
		RequireAuth:      cfg.Auth.Required,
		HandshakeTimeout: cfg.Limits.HandshakeTimeout,
	})
	return &Server{
		cfg:      cfg,
		store:    store,
		log:      log,
		mach:     mach,
		sem:      make(chan struct{}, cfg.Limits.MaxConcurrentConnections),
		shutdown: make(chan struct{}),
	}, nil
}

// Serve accepts until the listener closes or ctx is canceled.
func (s *Server) Serve(ctx context.Context, ln net.Listener) error {
	s.ln = ln
	s.log.Info("socks5_listening", Fields{
		"listen":          ln.Addr().String(),
		"auth_required":   s.cfg.Auth.Required,
		"max_connections": s.cfg.Limits.MaxConcurrentConnections,
	})

	go func() {
		select {
		case <-ctx.Done():
			_ = ln.Close()
		case <-s.shutdown:
			_ = ln.Close()
		}
	}()

	for {
		conn, err := ln.Accept()
		if err != nil {
			if s.isClosed() || errors.Is(err, net.ErrClosed) || ctx.Err() != nil {
				s.wg.Wait()
				return nil
			}
			s.log.Error("socks5_accept_error", Fields{"error": err.Error()})
			continue
		}
		s.totalConns.Add(1)
		select {
		case s.sem <- struct{}{}:
		default:
			// At capacity: reject deterministically rather than queueing
			// unbounded accepted sockets.
			s.rejectedConns.Add(1)
			s.log.Warn("socks5_at_capacity", Fields{"client": conn.RemoteAddr().String()})
			_ = conn.Close()
			continue
		}
		s.active.Add(1)
		s.wg.Add(1)
		go func(c net.Conn) {
			defer func() {
				<-s.sem
				s.active.Add(-1)
				s.wg.Done()
				_ = c.Close()
			}()
			s.handle(ctx, c)
		}(conn)
	}
}

// Shutdown stops accepting and waits briefly for in-flight sessions.
func (s *Server) Shutdown(timeout time.Duration) {
	if s.closed.CompareAndSwap(false, true) {
		close(s.shutdown)
		if s.ln != nil {
			_ = s.ln.Close()
		}
	}
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(timeout):
	}
}

func (s *Server) isClosed() bool { return s.closed.Load() }

// handle runs one session end to end and always writes a terminal record.
func (s *Server) handle(ctx context.Context, conn net.Conn) {
	id := newRequestID()
	client := conn.RemoteAddr().String()
	rl := s.log.WithRequest(id, client)
	start := time.Now().UTC()

	rec := policy.RequestRecord{
		RequestID:  id,
		StartedAt:  start,
		ClientAddr: client,
		Stage:      string(proto.StageMethod),
	}
	rowID, err := s.store.LogRequest(ctx, rec)
	if err != nil {
		rl.Error("socks5_persist_failed", Fields{"error": err.Error()})
		rowID = -1
	}

	rl.Step("method_negotiation_start", "server.handle", nil)
	out := s.mach.Handshake(ctx, conn)
	rec.Stage = string(out.Stage)
	rec.Username = out.User
	if out.Target != nil {
		rec.TargetHost = out.Target.Host()
		rec.TargetPort = int(out.Target.Port)
	}

	if out.Kind != proto.KindRelayReady {
		s.finishHandshakeFailure(ctx, rowID, rec, out, rl)
		return
	}

	// Successful connect: relay.
	dial := out.Upstream
	rec.Resolved = dial.Resolved
	rec.Attempts = attemptsToMaps(dial.Attempts)
	rl.Step("connect_established", "server.handle", Fields{
		"target":   rec.TargetHost,
		"port":     rec.TargetPort,
		"bound":    dial.Bound.String(),
		"resolved": addrsToStrings(dial.Resolved),
		"attempts": len(dial.Attempts),
	})

	st, kind := relay.Relay(conn, dial.Conn, relay.Budgets{
		MaxBytesUp:   s.cfg.Limits.MaxBytesUp,
		MaxBytesDown: s.cfg.Limits.MaxBytesDown,
		IdleTimeout:  s.cfg.Limits.IdleTimeout,
	})

	rec.Stage = string(proto.StageRelay)
	rec.OutcomeKind = string(kind)
	rec.BytesUp = st.Up
	rec.BytesDown = st.Down
	if kind != proto.KindRelayEOF {
		rec.Detail = fmt.Sprintf("relay ended: up_kind=%s down_kind=%s", st.UpKind, st.DownKind)
		rl.Failure("relay", string(kind), rec.Detail, "", Fields{
			"bytes_up": st.Up, "bytes_down": st.Down,
		})
	} else {
		rl.Step("relay_completed", "relay", Fields{
			"bytes_up": st.Up, "bytes_down": st.Down,
		})
	}
	s.persistFinish(ctx, rowID, rec, rl)
}

func (s *Server) finishHandshakeFailure(ctx context.Context, rowID int64, rec policy.RequestRecord,
	out *proto.SessionOutcome, rl *RequestLogger) {
	kind := out.Kind
	rec.OutcomeKind = string(kind)
	rec.Detail = out.Detail
	uncertainty := uncertaintyFor(kind)
	rl.Failure(string(out.Stage), string(kind), out.Detail, uncertainty, Fields{
		"method":         methodName(out.Method),
		"username":       out.User,
		"target":         rec.TargetHost,
		"greeting_bytes": out.GreetingBytes,
		"auth_bytes":     out.AuthBytes,
		"request_bytes":  out.RequestBytes,
	})
	s.persistFinish(ctx, rowID, rec, rl)
}

func (s *Server) persistFinish(ctx context.Context, rowID int64, rec policy.RequestRecord, rl *RequestLogger) {
	if rowID < 0 {
		return
	}
	if err := s.store.FinishRequest(ctx, rowID, rec); err != nil {
		rl.Error("socks5_persist_finish_failed", Fields{"error": err.Error()})
	}
}

// uncertaintyFor lists conclusions that depend on external behavior and
// therefore cannot be asserted with certainty from the proxy alone.
func uncertaintyFor(k proto.Kind) string {
	switch k {
	case proto.KindResolveFailed:
		return "resolution outcome depends on the external resolver; a transient resolver outage is indistinguishable from NXDOMAIN here"
	case proto.KindDialTimeout:
		return "timeout may reflect loss, filtering or a slow host; the proxy cannot distinguish"
	case proto.KindRelayPeerReset:
		return "a reset may originate from either endpoint or an intermediary"
	default:
		return ""
	}
}

func attemptsToMaps(as []proto.DialAttempt) []map[string]string {
	out := make([]map[string]string, 0, len(as))
	for _, a := range as {
		m := map[string]string{
			"addr":    a.AddrPort.String(),
			"outcome": a.Outcome,
		}
		if a.Err != "" {
			m["error"] = a.Err
		}
		out = append(out, m)
	}
	return out
}

func addrsToStrings(as []netip.Addr) []string {
	out := make([]string, 0, len(as))
	for _, a := range as {
		out = append(out, a.String())
	}
	return out
}

func methodName(m byte) string {
	switch m {
	case wire.MethodUserPass:
		return "username_password"
	case wire.MethodNoAuth:
		return "none"
	default:
		return fmt.Sprintf("0x%02x", m)
	}
}

// newRequestID returns an unguessable correlation id.
func newRequestID() string {
	var b [12]byte
	_, _ = rand.Read(b[:])
	return "req_" + hex.EncodeToString(b[:])
}

// ActiveCount exposes the current in-flight connection count.
func (s *Server) ActiveCount() int64 { return s.active.Load() }
