// Package server binds the SMTP protocol state machine to TCP listeners
// and enforces process-level limits (concurrent sessions).
package server

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net"
	"sync"

	"smtpsink/internal/config"
	"smtpsink/internal/protocol"
	"smtpsink/internal/storage"
)

// Server accepts SMTP connections and runs protocol sessions against a
// store, bounded by a concurrent-session semaphore.
type Server struct {
	cfg    config.Config
	store  storage.Store
	logger *log.Logger
	sem    chan struct{}

	mu     sync.Mutex
	ln     net.Listener
	closed bool
	wg     sync.WaitGroup
}

// New builds a Server. logger may be nil to disable logging.
func New(cfg config.Config, store storage.Store, logger *log.Logger) *Server {
	return &Server{
		cfg:    cfg,
		store:  store,
		logger: logger,
		sem:    make(chan struct{}, cfg.MaxSessions),
	}
}

// Addr returns the bound address, valid after ListenAndServe starts.
func (s *Server) Addr() net.Addr {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ln == nil {
		return nil
	}
	return s.ln.Addr()
}

// ListenAndServe binds cfg.Listen and serves until Shutdown or a fatal
// accept error.
func (s *Server) ListenAndServe(ctx context.Context) error {
	ln, err := net.Listen("tcp", s.cfg.Listen)
	if err != nil {
		return fmt.Errorf("server: listen %s: %w", s.cfg.Listen, err)
	}
	s.mu.Lock()
	s.ln = ln
	s.mu.Unlock()
	s.logf("event=listen addr=%s", ln.Addr())

	go func() {
		<-ctx.Done()
		s.Shutdown()
	}()

	for {
		conn, err := ln.Accept()
		if err != nil {
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed || errors.Is(err, net.ErrClosed) {
				s.wg.Wait()
				return nil
			}
			return fmt.Errorf("server: accept: %w", err)
		}
		select {
		case s.sem <- struct{}{}:
			s.wg.Add(1)
			go func() {
				defer s.wg.Done()
				defer func() { <-s.sem }()
				s.handle(conn)
			}()
		default:
			s.logf("event=reject remote=%s reason=session-limit limit=%d",
				conn.RemoteAddr(), s.cfg.MaxSessions)
			fmt.Fprintf(conn, "421 4.3.2 %s Service not available, too many sessions\r\n", s.cfg.Hostname)
			conn.Close()
		}
	}
}

// Shutdown stops accepting and waits for active sessions to finish.
func (s *Server) Shutdown() {
	s.mu.Lock()
	s.closed = true
	ln := s.ln
	s.mu.Unlock()
	if ln != nil {
		ln.Close()
	}
}

func (s *Server) handle(conn net.Conn) {
	defer conn.Close()
	sid := newSessionID()
	s.logf("sid=%s event=connect remote=%s", sid, conn.RemoteAddr())
	sess := &protocol.Session{
		ID:       sid,
		Hostname: s.cfg.Hostname,
		Domains:  protocol.DomainsFromList(s.cfg.LocalDomains),
		Limits: protocol.Limits{
			MaxLineBytes:    s.cfg.MaxLineBytes,
			MaxMessageBytes: s.cfg.MaxMessageBytes,
			MaxRecipients:   s.cfg.MaxRecipients,
		},
		Store:  s.store,
		Logger: s.logger,
	}
	err := sess.Serve(conn, conn.RemoteAddr().String())
	if err != nil {
		s.logf("sid=%s event=close reason=%v", sid, err)
		return
	}
	s.logf("sid=%s event=close reason=clean", sid)
}

func (s *Server) logf(format string, args ...any) {
	if s.logger != nil {
		s.logger.Printf(format, args...)
	}
}

func newSessionID() string {
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(fmt.Sprintf("server: crypto/rand unavailable: %v", err))
	}
	return "s-" + hex.EncodeToString(b[:])
}
