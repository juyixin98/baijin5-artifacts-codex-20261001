package client_test

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"

	"localstun/internal/client"
	"localstun/internal/server"
	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

// startRealServer runs the actual server on loopback.
func startRealServer(t *testing.T, addr string, key []byte) (*server.Server, *net.UDPAddr) {
	t.Helper()
	s, err := server.New(server.Config{
		ListenAddr:  addr,
		SharedKey:   key,
		Fingerprint: len(key) > 0,
	})
	if err != nil {
		t.Fatalf("server.New: %v", err)
	}
	errc := s.Serve()
	t.Cleanup(func() {
		_ = s.Close()
		if err := <-errc; err != nil {
			t.Logf("server exit: %v", err)
		}
	})
	deadline := time.Now().Add(2 * time.Second)
	for s.Addr() == nil && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if s.Addr() == nil {
		t.Fatal("server never bound")
	}
	return s, s.Addr()
}

// blackholeUDP binds a loopback socket that accepts (and ignores) datagrams,
// so requests time out without an ICMP port-unreachable error.
func blackholeUDP(t *testing.T) *net.UDPAddr {
	t.Helper()
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn.LocalAddr().(*net.UDPAddr)
}

func TestRoundTrip_IPv4_HappyPath(t *testing.T) {
	key := []byte("e2e-key")
	_, addr := startRealServer(t, "127.0.0.1:0", key)

	var mu sync.Mutex
	var events []client.Event
	c, err := client.New(client.Config{
		ServerAddr:  addr,
		SharedKey:   key,
		Fingerprint: true,
		Timeout:     time.Second,
		OnEvent: func(e client.Event) {
			mu.Lock()
			events = append(events, e)
			mu.Unlock()
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	res, err := c.RoundTrip(context.Background())
	if err != nil {
		t.Fatalf("RoundTrip: %v (kind=%s)", err, stunerror.Of(err))
	}
	if !res.Verified {
		t.Fatal("response must be integrity-verified")
	}
	if res.IP.To4() == nil || res.IP.String() != "127.0.0.1" {
		t.Fatalf("mapped ip=%s, want 127.0.0.1", res.IP)
	}
	if res.SrcAddr.String() != addr.String() {
		t.Fatalf("source=%s, want server %s", res.SrcAddr, addr)
	}
	if res.Port != c.LocalAddr().Port {
		t.Fatalf("mapped port=%d, want client ephemeral %d", res.Port, c.LocalAddr().Port)
	}
	if res.TxID == (stun.TransactionID{}) {
		t.Fatal("txid must be populated")
	}
	mu.Lock()
	gotSend, gotMatch := false, false
	for _, e := range events {
		if e.Type == client.EvSend {
			gotSend = true
		}
		if e.Type == client.EvRecvMatched {
			gotMatch = true
		}
	}
	mu.Unlock()
	if !gotSend || !gotMatch {
		t.Fatalf("missing state events: send=%v matched=%v", gotSend, gotMatch)
	}
}

func TestRoundTrip_IPv6_HappyPath(t *testing.T) {
	if !loopbackIPv6Available() {
		t.Skip("IPv6 loopback not available in this environment")
	}
	key := []byte("v6-key")
	_, addr := startRealServer(t, "[::1]:0", key)

	c, err := client.New(client.Config{
		ServerAddr:  addr,
		LocalAddr:   &net.UDPAddr{IP: net.IPv6loopback, Port: 0},
		SharedKey:   key,
		Fingerprint: true,
		Timeout:     time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	res, err := c.RoundTrip(context.Background())
	if err != nil {
		t.Fatalf("RoundTrip v6: %v (kind=%s)", err, stunerror.Of(err))
	}
	if !res.Verified {
		t.Fatal("v6 response must be verified")
	}
	if res.IP.To4() != nil || res.IP.String() != "::1" {
		t.Fatalf("mapped v6 ip = %s, want ::1", res.IP)
	}
	if res.Port != c.LocalAddr().Port {
		t.Fatalf("mapped port = %d want %d", res.Port, c.LocalAddr().Port)
	}
}

func TestRoundTrip_TimeoutIsExhaustion(t *testing.T) {
	addr := blackholeUDP(t)
	c, err := client.New(client.Config{ServerAddr: addr, Timeout: 150 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	start := time.Now()
	_, err = c.RoundTrip(context.Background())
	elapsed := time.Since(start)
	if stunerror.Of(err) != stunerror.KindExhausted {
		t.Fatalf("kind=%s want exhausted (err=%v)", stunerror.Of(err), err)
	}
	if elapsed < 120*time.Millisecond || elapsed > time.Second {
		t.Fatalf("timeout took %s, want ~150ms", elapsed)
	}
}

func TestRoundTrip_ContextCancelIsStateConflict(t *testing.T) {
	addr := blackholeUDP(t)
	c, err := client.New(client.Config{ServerAddr: addr, Timeout: 5 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()
	_, err = c.RoundTrip(ctx)
	if stunerror.Of(err) != stunerror.KindState {
		t.Fatalf("kind=%s want state", stunerror.Of(err))
	}
}

func TestResponseSourceMismatchIsIgnored(t *testing.T) {
	fake, txids, replies := newFakeSTUN(t, "127.0.0.1:0")

	var mu sync.Mutex
	var sawMismatch bool
	c, err := client.New(client.Config{
		ServerAddr: fake.LocalAddr().(*net.UDPAddr),
		Timeout:    time.Second,
		OnEvent: func(e client.Event) {
			if e.Type == client.EvRecvBadSource {
				mu.Lock()
				sawMismatch = true
				mu.Unlock()
			}
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	type rtResult struct {
		res *client.Result
		err error
	}
	done := make(chan rtResult, 1)
	go func() {
		res, err := c.RoundTrip(context.Background())
		done <- rtResult{res, err}
	}()
	txid := <-txids // request has now reached the fake

	// A spoofer on its own ephemeral socket sends a txid-correct response
	// directly to the client: source port differs from the configured server.
	spoofer, err := net.DialUDP("udp4", nil, c.LocalAddr())
	if err != nil {
		t.Fatal(err)
	}
	defer spoofer.Close()
	if _, err := spoofer.Write(buildResponse(txid, "203.0.113.5", 4444)); err != nil {
		t.Fatal(err)
	}
	waitFor(100 * time.Millisecond)

	// Genuine response from the fake's socket must still complete the request.
	replies <- responseCmd{txid: txid, ip: "198.51.100.1", port: c.LocalAddr().Port}

	r := <-done
	if r.err != nil {
		t.Fatalf("RoundTrip: %v", r.err)
	}
	if r.res.IP.String() != "198.51.100.1" {
		t.Fatalf("accepted wrong-source result: %s", r.res.IP)
	}
	mu.Lock()
	ok := sawMismatch
	mu.Unlock()
	if !ok {
		t.Fatal("expected a response_source_mismatch event")
	}
}

func TestOldResponseCannotCompleteNewRequest(t *testing.T) {
	fake, txids, replies := newFakeSTUN(t, "127.0.0.1:0")

	var mu sync.Mutex
	var sawUnknown bool
	c, err := client.New(client.Config{
		ServerAddr: fake.LocalAddr().(*net.UDPAddr),
		Timeout:    200 * time.Millisecond,
		OnEvent: func(e client.Event) {
			if e.Type == client.EvRecvUnknownTx {
				mu.Lock()
				sawUnknown = true
				mu.Unlock()
			}
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	// Request 1: sent, never answered, times out.
	r1 := make(chan error, 1)
	go func() {
		_, err := c.RoundTrip(context.Background())
		r1 <- err
	}()
	txidOld := <-txids
	if err := <-r1; stunerror.Of(err) != stunerror.KindExhausted {
		t.Fatalf("first request should time out, got kind=%s", stunerror.Of(err))
	}

	// Request 2 starts on the same socket.
	type rtResult struct {
		res *client.Result
		err error
	}
	done := make(chan rtResult, 1)
	go func() {
		res, err := c.RoundTrip(context.Background())
		done <- rtResult{res, err}
	}()
	txidNew := <-txids
	waitFor(50 * time.Millisecond)

	// A late response for the OLD transaction arrives while request 2 waits.
	replies <- responseCmd{txid: txidOld, ip: "192.0.2.200", port: 1}
	waitFor(100 * time.Millisecond)
	select {
	case r := <-done:
		t.Fatalf("new request completed from stale response: res=%v err=%v", r.res, r.err)
	default:
	}

	// The correct response for request 2 completes it.
	replies <- responseCmd{txid: txidNew, ip: "198.51.100.2", port: c.LocalAddr().Port}
	select {
	case r := <-done:
		if r.err != nil {
			t.Fatalf("second request: %v", r.err)
		}
		if r.res.IP.String() != "198.51.100.2" || r.res.TxID != txidNew {
			t.Fatalf("wrong completion: ip=%s txmatch=%v", r.res.IP, r.res.TxID == txidNew)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("second request never completed after its real response")
	}

	mu.Lock()
	ok := sawUnknown
	mu.Unlock()
	if !ok {
		t.Fatal("expected a response_unknown_transaction event for the stale packet")
	}
}

func TestTamperedResponseIsIntegrityFailure(t *testing.T) {
	key := []byte("integrate")
	fake, txids, replies := newFakeSTUN(t, "127.0.0.1:0", withKey(key), tamperNext())

	c, err := client.New(client.Config{
		ServerAddr:  fake.LocalAddr().(*net.UDPAddr),
		SharedKey:   key,
		Fingerprint: true,
		Timeout:     time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	done := make(chan resultOrErr, 1)
	go func() {
		res, err := c.RoundTrip(context.Background())
		done <- resultOrErr{res, err}
	}()
	txid := <-txids
	replies <- responseCmd{txid: txid, ip: "198.51.100.3", port: c.LocalAddr().Port}
	r := <-done
	if stunerror.Of(r.err) != stunerror.KindIntegrity {
		t.Fatalf("tampered response kind=%s want integrity", stunerror.Of(r.err))
	}
}

type resultOrErr struct {
	res *client.Result
	err error
}

func TestMaxOutstandingTableExhaustion(t *testing.T) {
	fake, txids, replies := newFakeSTUN(t, "127.0.0.1:0")
	c, err := client.New(client.Config{
		ServerAddr:     fake.LocalAddr().(*net.UDPAddr),
		Timeout:        5 * time.Second,
		MaxOutstanding: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	type rtResult struct {
		res *client.Result
		err error
	}
	done := make(chan rtResult, 1)
	go func() {
		res, err := c.RoundTrip(context.Background())
		done <- rtResult{res, err}
	}()
	firstTx := <-txids // request 1 occupies the single slot
	waitFor(50 * time.Millisecond)

	_, err = c.RoundTrip(context.Background())
	if stunerror.Of(err) != stunerror.KindExhausted {
		t.Fatalf("concurrent request kind=%s want exhausted", stunerror.Of(err))
	}

	// Release request 1 deterministically.
	replies <- responseCmd{txid: firstTx, ip: "198.51.100.4", port: c.LocalAddr().Port}
	if r := <-done; r.err != nil {
		t.Fatalf("first request: %v", r.err)
	}
}

func loopbackIPv6Available() bool {
	conn, err := net.ListenPacket("udp6", "[::1]:0")
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}
