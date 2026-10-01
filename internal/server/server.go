// Package server hosts the protocol state machine on a loopback-only TCP
// listener. It enforces connection/session budgets and deadlines, and treats
// every dropped connection as an aborted transaction (nothing is delivered
// until the sink commits after the final dot).
package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"sync"
	"time"

	"smtpsink/internal/config"
	"smtpsink/internal/protocol"
	"smtpsink/internal/wire"
)

// Server is the controlled SMTP service.
type Server struct {
	cfg  config.Config
	sink protocol.Sink
	log  *slog.Logger
	ln   net.Listener
	sem  chan struct{}

	mu       sync.Mutex
	closed   bool
	active   int
	activeWg sync.WaitGroup
}

// New creates a server but does not bind the port until Serve/Start.
func New(cfg config.Config, sink protocol.Sink, log *slog.Logger) *Server {
	if log == nil {
		log = slog.Default()
	}
	return &Server{
		cfg:  cfg,
		sink: sink,
		log:  log,
		// Concurrency gate: at most this many simultaneous sessions.
		sem: make(chan struct{}, 8),
	}
}

// Start binds the listener. Callers pair it with Close.
func (s *Server) Start(ctx context.Context) error {
	ln, err := net.Listen("tcp", s.cfg.Address())
	if err != nil {
		return fmt.Errorf("server: listen %s: %w", s.cfg.Address(), err)
	}
	s.ln = ln
	s.log.Info("smtpsink listening (loopback only)",
		"addr", ln.Addr().String(), "domains", s.cfg.LocalDomains)
	go s.acceptLoop(ctx)
	return nil
}

// Addr reports the bound address (useful when tests bind port 0).
func (s *Server) Addr() net.Addr { return s.ln.Addr() }

func (s *Server) acceptLoop(ctx context.Context) {
	for {
		conn, err := s.ln.Accept()
		if err != nil {
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed {
				return
			}
			if errors.Is(err, net.ErrClosed) {
				return
			}
			s.log.Error("accept failed", "err", err.Error())
			return
		}
		select {
		case s.sem <- struct{}{}:
		default:
			s.log.Warn("connection refused: at capacity")
			s.send421AndClose(conn)
			continue
		}
		s.activeWg.Add(1)
		go func(c net.Conn) {
			defer s.activeWg.Done()
			defer func() { <-s.sem }()
			s.handle(ctx, c)
		}(conn)
	}
}

func (s *Server) send421AndClose(c net.Conn) {
	_ = wire.Reply(c, 421, "service busy, try later")
	_ = c.Close()
}

func (s *Server) handle(parent context.Context, conn net.Conn) {
	remote := conn.RemoteAddr().String()
	defer conn.Close()

	idle := time.Duration(s.cfg.Timeouts.IdleMS) * time.Millisecond
	session := time.Duration(s.cfg.Timeouts.SessionMS) * time.Millisecond
	deadline := time.Now().Add(session)
	ctx, cancel := context.WithDeadline(parent, deadline)
	defer cancel()
	_ = conn.SetDeadline(deadline)

	sessCfg := protocol.Config{
		Hostname: s.cfg.Hostname,
		Policy:   protocol.Policy{LocalDomains: s.cfg.LocalDomains},
		Limits:   s.cfg.Limits,
		Sink:     s.sink,
		Logger:   s.log,
	}
	sess := protocol.NewSession(sessCfg)
	rd := wire.NewReader(conn, s.cfg.Limits.CommandLineBytes)

	log := s.log.With("session_id", sess.ID(), "remote", remote)
	log.Info("connection opened", "state", "init")
	defer func() { log.Info("connection closed", "state", sess.Phase().String()) }()

	greeting := sess.Greeting()
	if err := wire.Reply(conn, greeting.Code, greeting.Text...); err != nil {
		log.Warn("could not send greeting", "err", err.Error())
		return
	}

	for {
		if err := conn.SetReadDeadline(time.Now().Add(idle)); err != nil {
			return
		}
		line, err := rd.ReadLine()
		if err != nil {
			if errors.Is(err, wire.ErrLineTooLong) {
				// Physical line consumed and resynchronized; keep the session.
				log.Warn("over-long command line rejected", "category", "line-too-long")
				if werr := wire.Reply(conn, 500, "line too long"); werr != nil {
					return
				}
				continue
			}
			s.handleReadError(conn, err, log)
			return
		}
		reply := sess.Feed(ctx, line)

		if reply.Code == 354 {
			if err := wire.Reply(conn, reply.Code, reply.Text...); err != nil {
				log.Warn("write failed before DATA", "err", err.Error())
				return
			}
			// DATA collection has its own state/logic and may close silently.
			dreply := sess.HandleData(ctx, rd)
			if dreply.Silent {
				return
			}
			if err := wire.Reply(conn, dreply.Code, dreply.Text...); err != nil {
				log.Warn("write failed after DATA", "err", err.Error())
				return
			}
			if dreply.Close {
				return
			}
			continue
		}

		if reply.Silent {
			return
		}
		if err := wire.Reply(conn, reply.Code, reply.Text...); err != nil {
			log.Warn("write failed", "err", err.Error())
			return
		}
		if reply.Close {
			return
		}
	}
}

func (s *Server) handleReadError(conn net.Conn, err error, log *slog.Logger) {
	switch {
	case errors.Is(err, io.EOF):
		log.Info("peer disconnected cleanly", "category", "client-closed")
	case isTimeout(err):
		log.Warn("idle/session timeout, closing", "category", "timeout")
		_ = wire.Reply(conn, 421, "timeout closing connection")
	case errors.Is(err, wire.ErrFatalOverflow):
		log.Error("unrecoverable framing overflow, closing", "category", "fatal-overflow")
		_ = wire.Reply(conn, 421, "framing error")
	default:
		log.Warn("read error, closing", "category", "io-error", "err", err.Error())
	}
}

func isTimeout(err error) bool {
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout()
}

// Close stops accepting and waits briefly for in-flight sessions.
func (s *Server) Close() error {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return nil
	}
	s.closed = true
	ln := s.ln
	s.mu.Unlock()
	if ln != nil {
		_ = ln.Close()
	}
	done := make(chan struct{})
	go func() { s.activeWg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		s.log.Warn("shutdown timed out waiting for sessions")
	}
	return nil
}
