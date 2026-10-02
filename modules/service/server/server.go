// Package server implements the controlled HTTP/2 service that owns one
// HPACK encoder and decoder PER CONNECTION, assembles header blocks across
// HEADERS/CONTINUATION, persists results in SQLite and emits structured,
// request-correlated logs.
package server

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"sync"

	"hpacklab.local/service/config"
	"hpacklab.local/service/store"
)

// Server is the hpackd HTTP/2 service.
type Server struct {
	cfg   config.Config
	store *store.Store
	log   *slog.Logger

	listener net.Listener
	wg       sync.WaitGroup
	mu       sync.Mutex
	closed   bool
}

// New builds a Server. Call Serve to start accepting.
func New(cfg config.Config, st *store.Store, log *slog.Logger) *Server {
	return &Server{cfg: cfg, store: st, log: log}
}

// Addr reports the listener address once serving.
func (s *Server) Addr() net.Addr {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.listener == nil {
		return nil
	}
	return s.listener.Addr()
}

// Serve accepts connections until Shutdown or the listener errors.
func (s *Server) Serve(ctx context.Context) error {
	ln, err := s.listen()
	if err != nil {
		return err
	}
	s.mu.Lock()
	s.listener = ln
	s.mu.Unlock()
	s.log.Info("hpackd listening",
		"addr", ln.Addr().String(),
		"tls", s.cfg.Server.CertFile != "",
		"hpack_table_size", s.cfg.Hpack.TableSize,
		"version", "hpackd/1.0 (RFC 7541, RFC 7540)")

	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()

	for {
		rawConn, err := ln.Accept()
		if err != nil {
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed || ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("accept: %w", err)
		}
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			c := newConn(rawConn, s)
			c.run(ctx)
		}()
	}
}

func (s *Server) listen() (net.Listener, error) {
	if s.cfg.Server.CertFile != "" {
		cert, err := tls.LoadX509KeyPair(s.cfg.Server.CertFile, s.cfg.Server.KeyFile)
		if err != nil {
			return nil, fmt.Errorf("load keypair: %w", err)
		}
		ln, err := tls.Listen("tcp", s.cfg.Server.Listen, &tls.Config{
			Certificates: []tls.Certificate{cert},
			MinVersion:   tls.VersionTLS12,
			NextProtos:   []string{"h2"},
		})
		if err != nil {
			return nil, fmt.Errorf("tls listen: %w", err)
		}
		return ln, nil
	}
	// h2c (prior knowledge) for local tests.
	ln, err := net.Listen("tcp", s.cfg.Server.Listen)
	if err != nil {
		return nil, fmt.Errorf("listen: %w", err)
	}
	return ln, nil
}

// Shutdown stops accepting and waits for in-flight connections.
func (s *Server) Shutdown(ctx context.Context) error {
	s.mu.Lock()
	s.closed = true
	if s.listener != nil {
		_ = s.listener.Close()
	}
	s.mu.Unlock()
	done := make(chan struct{})
	go func() {
		s.wg.Wait()
		close(done)
	}()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// ignoreConnClose reports whether err is an expected connection teardown.
func ignoreConnClose(err error) bool {
	return errors.Is(err, net.ErrClosed)
}
