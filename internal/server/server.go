// Package server is the controlled-service boundary: a TCP listener that
// spawns one proto.Session per connection and shares one store and one
// event hub across all of them.
package server

import (
	"log"
	"net"
	"sync"

	"imapd/internal/errs"
	"imapd/internal/proto"
	"imapd/internal/store"
)

// Server accepts IMAP connections and drives sessions.
type Server struct {
	ln  net.Listener
	st  *store.Store
	hub *proto.Hub
	log *log.Logger
	wg  sync.WaitGroup
}

// Listen binds addr (e.g. "127.0.0.1:1143", or "127.0.0.1:0" in tests).
func Listen(addr string, st *store.Store, logger *log.Logger) (*Server, error) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, errs.Wrap(errs.CatResource, "server.listen", err, "cannot bind "+addr)
	}
	return &Server{ln: ln, st: st, hub: proto.NewHub(), log: logger}, nil
}

// Addr returns the bound address (useful with port 0).
func (s *Server) Addr() string { return s.ln.Addr().String() }

// Serve accepts connections until Close. Each connection gets its own
// session state machine but shares the store and the event hub.
func (s *Server) Serve() error {
	for {
		conn, err := s.ln.Accept()
		if err != nil {
			return err // listener closed
		}
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			sess := &proto.Session{Store: s.st, Hub: s.hub, Log: s.log}
			sess.Serve(conn)
		}()
	}
}

// Close stops accepting and waits for live sessions to finish.
func (s *Server) Close() {
	s.ln.Close()
	s.wg.Wait()
}
