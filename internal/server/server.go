// Package server is the controlled service layer: it wires the h2 state
// machine to TCP transport, enforces read/write deadlines, routes the small
// set of predefined requests, and journals diagnostics.
package server

import (
	"fmt"
	"io"
	"log"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"h2svc/internal/config"
	"h2svc/internal/frame"
	"h2svc/internal/h2"
	"h2svc/internal/hpack"
)

// Server accepts HTTP/2 connections on a TCP listener (prior-knowledge,
// no TLS — local controlled service).
type Server struct {
	cfg    config.Config
	logger h2.EventLogger
	ln     net.Listener

	connSeq  atomic.Uint64
	wg       sync.WaitGroup
	closing  atomic.Bool
	doneOnce sync.Once
	done     chan struct{}
}

// New creates a Server. logger may be nil.
func New(cfg config.Config, logger h2.EventLogger) *Server {
	return &Server{cfg: cfg, logger: logger, done: make(chan struct{})}
}

// Listen binds the configured address.
func (s *Server) Listen() error {
	ln, err := net.Listen("tcp", s.cfg.ListenAddr)
	if err != nil {
		return fmt.Errorf("server: listen %s: %w", s.cfg.ListenAddr, err)
	}
	s.ln = ln
	return nil
}

// Addr returns the bound address (valid after Listen).
func (s *Server) Addr() string {
	if s.ln == nil {
		return ""
	}
	return s.ln.Addr().String()
}

// Serve accepts connections until Close. It returns nil on orderly shutdown.
func (s *Server) Serve() error {
	for {
		nc, err := s.ln.Accept()
		if err != nil {
			if s.closing.Load() {
				return nil
			}
			return fmt.Errorf("server: accept: %w", err)
		}
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			s.serveConn(nc)
		}()
	}
}

// Close stops accepting and waits for in-flight connections.
func (s *Server) Close() error {
	s.closing.Store(true)
	s.doneOnce.Do(func() { close(s.done) })
	if s.ln != nil {
		_ = s.ln.Close()
	}
	s.wg.Wait()
	return nil
}

func (s *Server) serveConn(nc net.Conn) {
	defer nc.Close()
	id := s.connSeq.Add(1)
	c := h2.NewConn(id, h2.Config{
		MaxFrameSize:        s.cfg.MaxFrameSize,
		InitialRecvWindow:   s.cfg.InitialRecvWindow,
		SendQueueCapacity:   s.cfg.SendQueueCapacity,
		MaxPendingBodyBytes: s.cfg.MaxPendingBodyBytes,
	}, newRouter(s.cfg.LargeBodyBytes), s.logger)
	defer c.Close()

	// Client connection preface (RFC 7540 §3.5).
	if s.cfg.ReadTimeoutMs > 0 {
		_ = nc.SetReadDeadline(time.Now().Add(time.Duration(s.cfg.ReadTimeoutMs) * time.Millisecond))
	}
	preface := make([]byte, len(frame.ClientPreface))
	if _, err := io.ReadFull(nc, preface); err != nil {
		s.logf("conn %d: preface read: %v", id, err)
		return
	}
	if string(preface) != frame.ClientPreface {
		s.logf("conn %d: invalid client preface", id)
		return
	}
	if err := c.SendServerSettings(); err != nil {
		s.logf("conn %d: send settings: %v", id, err)
		return
	}

	writeErr := make(chan error, 1)
	go s.writerLoop(nc, c, writeErr)

	for {
		if s.cfg.ReadTimeoutMs > 0 {
			_ = nc.SetReadDeadline(time.Now().Add(time.Duration(s.cfg.ReadTimeoutMs) * time.Millisecond))
		}
		h, err := frame.ReadHeader(nc)
		if err != nil {
			if err != io.EOF && !s.closing.Load() {
				s.logf("conn %d: read header: %v", id, err)
			}
			return
		}
		// Bound the payload read by the advertised max frame size; the state
		// machine re-validates and produces the protocol error.
		if h.Length > s.cfg.MaxFrameSize+frame.DefaultMaxFrameSize {
			s.logf("conn %d: frame length %d far exceeds bound, closing", id, h.Length)
			return
		}
		payload := make([]byte, h.Length)
		if _, err := io.ReadFull(nc, payload); err != nil {
			s.logf("conn %d: read payload: %v", id, err)
			return
		}
		if err := c.HandleFrame(h, payload); err != nil {
			s.logf("conn %d: %v", id, err)
			// Drain the writer briefly so GOAWAY reaches the peer.
			select {
			case <-writeErr:
			case <-time.After(2 * time.Second):
			}
			return
		}
		select {
		case err := <-writeErr:
			s.logf("conn %d: writer: %v", id, err)
			return
		default:
		}
	}
}

// writerLoop drains the bounded outbound queue to the socket. After each
// write it kicks FlushAll so DATA stalled on a full queue is retried.
func (s *Server) writerLoop(nc net.Conn, c *h2.Conn, writeErr chan<- error) {
	for f := range c.Outbound() {
		if s.cfg.WriteTimeoutMs > 0 {
			_ = nc.SetWriteDeadline(time.Now().Add(time.Duration(s.cfg.WriteTimeoutMs) * time.Millisecond))
		}
		hdr := f.Header.Marshal()
		if _, err := nc.Write(hdr[:]); err != nil {
			writeErr <- err
			return
		}
		if len(f.Payload) > 0 {
			if _, err := nc.Write(f.Payload); err != nil {
				writeErr <- err
				return
			}
		}
		c.FlushAll()
	}
}

func (s *Server) logf(format string, args ...any) {
	log.Printf("h2svc "+format, args...)
}

// router implements h2.Handler for the predefined simple requests.
// One router exists per connection, so per-stream state cannot collide
// across connections.
type router struct {
	largeBodyBytes int
	mu             sync.Mutex
	paths          map[uint32]string
	echoBuf        map[uint32][]byte
}

func newRouter(largeBodyBytes int) *router {
	return &router{
		largeBodyBytes: largeBodyBytes,
		paths:          map[uint32]string{},
		echoBuf:        map[uint32][]byte{},
	}
}

// NewHandler returns the predefined-route request handler used by the
// server. Exported so compatibility tests can drive the real routing code
// through the h2 state machine without a socket.
func NewHandler(largeBodyBytes int) h2.Handler {
	return newRouter(largeBodyBytes)
}

func headerValue(fields []hpack.HeaderField, name string) string {
	for _, f := range fields {
		if strings.EqualFold(f.Name, name) {
			return f.Value
		}
	}
	return ""
}

func (r *router) OnHeaders(c *h2.Conn, s *h2.Stream) {
	method := headerValue(s.Headers, ":method")
	path := headerValue(s.Headers, ":path")
	r.mu.Lock()
	r.paths[s.ID] = path
	r.mu.Unlock()
	if method != "GET" && method != "POST" {
		respond(c, s.ID, 405, "method not allowed\n")
		return
	}
	switch path {
	case "/", "/echo", "/large":
		// handled below / in OnData
	default:
		respond(c, s.ID, 404, "not found\n")
		return
	}
	if path == "/echo" {
		return // respond when the request body completes (END_STREAM)
	}
	if path == "/large" {
		body := make([]byte, r.largeBodyBytes)
		for i := range body {
			body[i] = byte('a' + i%26)
		}
		respond(c, s.ID, 200, string(body))
		return
	}
	respond(c, s.ID, 200, "hello h2\n")
}

func (r *router) OnData(c *h2.Conn, s *h2.Stream, data []byte, endStream bool) {
	r.mu.Lock()
	path := r.paths[s.ID]
	if path != "/echo" {
		if endStream {
			delete(r.paths, s.ID)
		}
		r.mu.Unlock()
		return // non-echo routes ignore any request body
	}
	r.echoBuf[s.ID] = append(r.echoBuf[s.ID], data...)
	body := r.echoBuf[s.ID]
	if endStream {
		delete(r.echoBuf, s.ID)
		delete(r.paths, s.ID)
	}
	r.mu.Unlock()
	if endStream {
		respond(c, s.ID, 200, string(body))
	}
}

// respond sends a complete predefined response: HEADERS then DATA.
func respond(c *h2.Conn, streamID uint32, status int, body string) {
	fields := []hpack.HeaderField{
		{Name: ":status", Value: fmt.Sprintf("%d", status)},
		{Name: "content-type", Value: "text/plain"},
	}
	if err := c.SendHeaders(streamID, fields, false); err != nil {
		return
	}
	_ = c.SendBody(streamID, []byte(body), true)
}
