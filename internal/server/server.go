// Package server is the controlled IMAP service: it accepts connections,
// runs the per-connection protocol state machine, and maps typed errors from
// the lower layers onto IMAP statuses/response codes.
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

	"imaplite/internal/auth"
	"imaplite/internal/broker"
	"imaplite/internal/imapwire"
	"imaplite/internal/store"
)

// Limits are the resource budgets enforced per connection / command.
type Limits struct {
	MaxLiteral int
	MaxLine    int
}

// Server is the listening IMAP service.
type Server struct {
	addr   string
	store  *store.Store
	users  *auth.UserStore
	log    *slog.Logger
	broker *broker.Broker
	limits Limits

	listener net.Listener
	wg       sync.WaitGroup
	closeMu  sync.Mutex
	closed   bool
}

// Config configures a Server.
type Config struct {
	Addr   string
	Store  *store.Store
	Users  *auth.UserStore
	Logger *slog.Logger
	Limits Limits
}

// New builds a server (it does not start listening; call Serve/Listen).
func New(cfg Config) *Server {
	lim := cfg.Limits
	if lim.MaxLiteral == 0 {
		lim.MaxLiteral = imapwire.MaxLiteralDefault
	}
	if lim.MaxLine == 0 {
		lim.MaxLine = 1 << 20
	}
	return &Server{
		addr:   cfg.Addr,
		store:  cfg.Store,
		users:  cfg.Users,
		log:    cfg.Logger,
		broker: broker.New(),
		limits: lim,
	}
}

// Addr reports the bound address (useful when Serve bound ":0").
func (s *Server) Addr() string {
	if s.listener != nil {
		return s.listener.Addr().String()
	}
	return s.addr
}

// Broker is exposed for tests that drive mutations in-process.
func (s *Server) Broker() *broker.Broker { return s.broker }

// Listen binds the TCP port.
func (s *Server) Listen() error {
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return fmt.Errorf("listen %s: %w", s.addr, err)
	}
	s.listener = ln
	return nil
}

// Serve accepts connections until the server is closed.
func (s *Server) Serve(ctx context.Context) error {
	if s.listener == nil {
		if err := s.Listen(); err != nil {
			return err
		}
	}
	s.log.Info("imap service listening", "addr", s.Addr())
	for {
		conn, err := s.listener.Accept()
		if err != nil {
			s.closeMu.Lock()
			closed := s.closed
			s.closeMu.Unlock()
			if closed {
				return nil
			}
			return fmt.Errorf("accept: %w", err)
		}
		s.wg.Add(1)
		go func(c net.Conn) {
			defer s.wg.Done()
			sess := newSession(s, c)
			sess.run(ctx)
		}(conn)
	}
}

// Close stops listening and waits for sessions to finish.
func (s *Server) Close() error {
	s.closeMu.Lock()
	s.closed = true
	ln := s.listener
	s.closeMu.Unlock()
	if ln != nil {
		ln.Close()
	}
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
	return nil
}

// quietClose closes the transport ignoring the error.
func quietClose(c io.Closer) { _ = c.Close() }

// asWireError extracts the typed codec error if present.
func asWireError(err error) *imapwire.Error {
	var we *imapwire.Error
	if errors.As(err, &we) {
		return we
	}
	return nil
}
