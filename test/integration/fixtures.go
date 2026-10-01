// Package integration contains black-box, end-to-end tests that drive the
// proxy over real TCP sockets. It deliberately does not reuse the proxy's
// own client-side code: expectations are written against RFC 1928/1929 byte
// layouts by hand, and outcomes are read back independently from the SQLite
// request log and from captured log lines.
package integration

import (
	"bytes"
	"context"
	"io"
	"net"
	"net/netip"
	"sync"
	"testing"
	"time"

	"sockswhitelist/internal/config"
	"sockswhitelist/internal/gateway"
	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/server"
)

// countingDialer wraps the production dialer and records every address the
// proxy actually attempts, so tests can prove a denied target was never
// dialed.
type countingDialer struct {
	inner  gateway.Dialer
	mu     sync.Mutex
	dialed []string
}

func (d *countingDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	d.mu.Lock()
	d.dialed = append(d.dialed, address)
	d.mu.Unlock()
	return d.inner.DialContext(ctx, network, address)
}

func (d *countingDialer) snapshot() []string {
	d.mu.Lock()
	defer d.mu.Unlock()
	out := make([]string, len(d.dialed))
	copy(out, d.dialed)
	return out
}

// scriptResolver maps names to fixed addresses, emulating DNS with fully
// controlled, synthetic input.
type scriptResolver struct {
	hosts map[string][]netip.Addr
}

func (r scriptResolver) LookupNetIP(_ context.Context, _, host string) ([]netip.Addr, error) {
	return r.hosts[host], nil
}

// countingResolver wraps a resolver and records how often it was consulted,
// proving resolution was skipped when policy rejects the name outright.
type countingResolver struct {
	inner  gateway.Resolver
	mu     sync.Mutex
	calls  int
	onCall func()
}

func (r *countingResolver) LookupNetIP(ctx context.Context, network, host string) ([]netip.Addr, error) {
	r.mu.Lock()
	r.calls++
	cb := r.onCall
	r.mu.Unlock()
	if cb != nil {
		cb()
	}
	return r.inner.LookupNetIP(ctx, network, host)
}

// Fixture is a fully wired proxy plus its durable store and captured logs.
type Fixture struct {
	t      *testing.T
	Store  *policy.Store
	Server *server.Server
	logBuf *syncBuffer
	Dialer *countingDialer
	ln     net.Listener
	Addr   string
	cfg    config.Config
	cancel context.CancelFunc
}

type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// FixtureOptions tunes a fixture.
type FixtureOptions struct {
	AuthRequired bool
	MaxBytesUp   int64
	MaxBytesDown int64
	IdleTimeout  time.Duration
	MaxConns     int
	// Resolver maps synthetic domains; nil means no domain names resolve.
	Resolver gateway.Resolver
}

// NewFixture starts a proxy on a random loopback port with an in-memory
// SQLite database and a baseline local-only whitelist.
func NewFixture(t *testing.T, opts FixtureOptions) *Fixture {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())

	store, err := policy.Open(ctx, ":memory:")
	if err != nil {
		cancel()
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() {
		cancel()
		_ = store.Close()
	})

	rules := []policy.Rule{
		{Kind: policy.KindCIDR, Host: "127.0.0.0/8", Ports: []int{0}},
		{Kind: policy.KindCIDR, Host: "::1/128", Ports: []int{0}},
		{Kind: policy.KindDomain, Host: "*.local.test", Ports: []int{0}},
	}
	if err := store.ReplaceRules(ctx, rules); err != nil {
		t.Fatalf("rules: %v", err)
	}
	if opts.AuthRequired {
		if err := store.SetUser(ctx, "alice", "pw"); err != nil {
			t.Fatalf("adduser: %v", err)
		}
	}

	idle := opts.IdleTimeout
	if idle == 0 {
		idle = 5 * time.Second
	}
	maxConns := opts.MaxConns
	if maxConns == 0 {
		maxConns = 32
	}
	up := opts.MaxBytesUp
	if up == 0 {
		up = 64 * 1024 * 1024
	}
	down := opts.MaxBytesDown
	if down == 0 {
		down = 64 * 1024 * 1024
	}

	cfg := config.Config{
		Listen:   "127.0.0.1:0",
		Database: ":memory:",
		Auth:     config.Auth{Required: opts.AuthRequired},
		Limits: config.Limits{
			MaxConcurrentConnections: maxConns,
			HandshakeTimeout:         3 * time.Second,
			ResolveTimeout:           2 * time.Second,
			DialTimeout:              2 * time.Second,
			IdleTimeout:              idle,
			MaxBytesUp:               up,
			MaxBytesDown:             down,
		},
		Log: config.Log{Level: "debug"},
	}

	dialer := &countingDialer{inner: &net.Dialer{Timeout: 2 * time.Second}}
	var connector *gateway.Connector
	if opts.Resolver != nil {
		connector = &gateway.Connector{
			Store: store, Resolver: opts.Resolver, Dialer: dialer,
			ResolveTimeout: 2 * time.Second, DialTimeout: 2 * time.Second,
		}
	} else {
		connector = &gateway.Connector{
			Store:          store,
			Resolver:       scriptResolver{hosts: map[string][]netip.Addr{}},
			Dialer:         dialer,
			ResolveTimeout: 2 * time.Second, DialTimeout: 2 * time.Second,
		}
	}

	logBuf := &syncBuffer{}
	logger := server.NewLogger(logBuf, "debug", false)

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv, err := server.New(cfg, store, connector, logger)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	go func() { _ = srv.Serve(ctx, ln) }()
	t.Cleanup(func() {
		srv.Shutdown(2 * time.Second)
		_ = ln.Close()
	})

	return &Fixture{
		t: t, Store: store, Server: srv, logBuf: logBuf, Dialer: dialer,
		ln: ln, Addr: ln.Addr().String(), cfg: cfg, cancel: cancel,
	}
}

// startEcho starts a target server that echoes every byte it receives.
// listenHost selects the bind address ("127.0.0.1" or "::1"). When
// closeOnEOF is true the session half-closes after the client does.
func startEcho(t *testing.T, listenHost string) string {
	t.Helper()
	ln, err := net.Listen("tcp", net.JoinHostPort(listenHost, "0"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go echoSession(c)
		}
	}()
	return ln.Addr().String()
}

func echoSession(c net.Conn) {
	defer func() { _ = c.Close() }()
	_, _ = io.Copy(c, c) // echo until the peer FINs both directions
}

// startCollectServer reads everything it receives, then replies once with
// reply and half-closes. It exercises client half-close (FIN) semantics.
func startCollectServer(t *testing.T, reply []byte) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	go func() {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		go func() {
			defer func() { _ = c.Close() }()
			// Read until client FIN.
			buf := make([]byte, 4096)
			for {
				if _, err := c.Read(buf); err != nil {
					break
				}
			}
			_, _ = c.Write(reply)
			if tc, ok := c.(*net.TCPConn); ok {
				_ = tc.CloseWrite()
			}
		}()
	}()
	return ln.Addr().String()
}

// startSilentServer accepts and holds the connection without sending.
func startSilentServer(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	go func() {
		c, err := ln.Accept()
		if err == nil {
			// Keep the conn open; the test ends and cleanup closes listener.
			t.Cleanup(func() { _ = c.Close() })
		}
	}()
	return ln.Addr().String()
}

// startResetServer accepts and aborts the connection with an RST.
func startResetServer(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	go func() {
		c, err := ln.Accept()
		if err == nil {
			if tc, ok := c.(*net.TCPConn); ok {
				_ = tc.SetLinger(0)
			}
			_ = c.Close()
		}
	}()
	return ln.Addr().String()
}

// logText returns everything the proxy logged.
func (f *Fixture) logText() string { return f.logBuf.String() }
