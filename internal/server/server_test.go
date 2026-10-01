package server

import (
	"bytes"
	"context"
	"net"
	"testing"
	"time"

	"sockswhitelist/internal/config"
	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/proto"
	"sockswhitelist/internal/wire"
)

// blockingConnecter blocks until released, holding a connection in the
// CONNECT phase (and thus a concurrency slot).
type blockingConnecter struct {
	release chan struct{}
}

func (b *blockingConnecter) Connect(ctx context.Context, _ wire.Target) (*proto.DialResult, *proto.Failure) {
	select {
	case <-b.release:
	case <-ctx.Done():
	}
	return nil, proto.NewFailure(proto.KindDialFailed, "released")
}

func newTestServer(t *testing.T, maxConns int, connector proto.Connecter) (*Server, net.Listener, *policy.Store) {
	t.Helper()
	store, err := policy.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	cfg := config.Default()
	cfg.Database = ":memory:"
	cfg.Auth.Required = false
	cfg.Limits.MaxConcurrentConnections = maxConns
	cfg.Limits.HandshakeTimeout = 5 * time.Second
	var logBuf bytes.Buffer
	logger := NewLogger(&logBuf, "error", true)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(cfg, store, connector, logger)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go func() { _ = srv.Serve(ctx, ln) }()
	return srv, ln, store
}

// dialAndGreeting completes method+request but blocks inside Connect.
func dialToConnectPhase(t *testing.T, addr string) net.Conn {
	t.Helper()
	c, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	// no-auth method negotiation.
	if _, err := c.Write([]byte{5, 1, 0}); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 2)
	if _, err := readFull(c, buf); err != nil {
		t.Fatal(err)
	}
	// CONNECT 127.0.0.1:9 -> server enters blocking Connect.
	if _, err := c.Write([]byte{5, 1, 0, 1, 127, 0, 0, 1, 0, 9}); err != nil {
		t.Fatal(err)
	}
	return c
}

func readFull(c net.Conn, buf []byte) (int, error) {
	n := 0
	for n < len(buf) {
		m, err := c.Read(buf[n:])
		n += m
		if err != nil {
			return n, err
		}
	}
	return n, nil
}

func TestAtCapacityRejectsAndBounded(t *testing.T) {
	bc := &blockingConnecter{release: make(chan struct{})}
	srv, ln, _ := newTestServer(t, 1, bc)

	holder := dialToConnectPhase(t, ln.Addr().String())
	defer func() { _ = holder.Close() }()
	deadline := time.Now().Add(2 * time.Second)
	for srv.ActiveCount() != 1 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if srv.ActiveCount() != 1 {
		t.Fatalf("active=%d want 1", srv.ActiveCount())
	}

	// Capacity full: additional connections must be closed deterministically.
	for i := 0; i < 3; i++ {
		c, err := net.Dial("tcp", ln.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
		buf := make([]byte, 1)
		if _, err := c.Read(buf); err == nil {
			t.Fatalf("connection %d at capacity was serviced, want immediate close", i)
		}
		_ = c.Close()
	}
	if srv.ActiveCount() != 1 {
		t.Fatalf("active=%d, rejected conns must not acquire a slot", srv.ActiveCount())
	}
	if srv.rejectedConns.Load() < 3 {
		t.Fatalf("rejected counter=%d want >=3", srv.rejectedConns.Load())
	}
	close(bc.release)
}

func TestNewDefaultsAndRequestID(t *testing.T) {
	store, err := policy.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()
	cfg := config.Default()
	srv, err := New(cfg, store, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if srv == nil {
		t.Fatal("nil server")
	}
	ids := map[string]bool{}
	for i := 0; i < 100; i++ {
		id := newRequestID()
		if len(id) != len("req_")+24 {
			t.Fatalf("bad id format %q", id)
		}
		if ids[id] {
			t.Fatalf("duplicate request id %q", id)
		}
		ids[id] = true
	}
}
