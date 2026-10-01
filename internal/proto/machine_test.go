package proto

import (
	"context"
	"errors"
	"io"
	"net"
	"net/netip"
	"testing"
	"time"

	"sockswhitelist/internal/wire"
)

// fakeAuth is a scripted authenticator.
type fakeAuth struct {
	user, pass string
	err        error
}

func (a fakeAuth) Authenticate(_ context.Context, user, pass string) (bool, error) {
	if a.err != nil {
		return false, a.err
	}
	return user == a.user && pass == a.pass, nil
}

// fakeConnect either returns a pipe-backed success or a categorized failure.
type fakeConnect struct {
	result *DialResult
	fail   *Failure
	called bool
	target wire.Target
}

func (c *fakeConnect) Connect(_ context.Context, t wire.Target) (*DialResult, *Failure) {
	c.called = true
	c.target = t
	if c.fail != nil {
		return nil, c.fail
	}
	return c.result, nil
}

// machineHarness starts a listener and runs one Handshake per raw client.
type machineHarness struct {
	t   *testing.T
	mac *Machine
	ln  net.Listener
}

func newMachineHarness(t *testing.T, mac *Machine) *machineHarness {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	return &machineHarness{t: t, mac: mac, ln: ln}
}

// run accepts one connection, runs Handshake, returns the outcome and the
// upstream connection (which the caller must close if non-nil).
func (h *machineHarness) run() (*SessionOutcome, net.Conn) {
	conn, err := h.ln.Accept()
	if err != nil {
		h.t.Fatal(err)
	}
	out := h.mac.Handshake(context.Background(), conn)
	var up net.Conn
	if out.Upstream != nil {
		up = out.Upstream.Conn
	}
	return out, up
}

func (h *machineHarness) dial() net.Conn {
	c, err := net.Dial("tcp", h.ln.Addr().String())
	if err != nil {
		h.t.Fatal(err)
	}
	return c
}

// segmented writes p one byte at a time with a small delay.
func segmented(t *testing.T, c net.Conn, p []byte) {
	t.Helper()
	for _, b := range p {
		if _, err := c.Write([]byte{b}); err != nil {
			t.Fatalf("segmented write: %v", err)
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func readN(t *testing.T, c net.Conn, n int) []byte {
	t.Helper()
	buf := make([]byte, n)
	if _, err := io.ReadFull(c, buf); err != nil {
		t.Fatalf("read %d bytes: %v", n, err)
	}
	return buf
}

func authMachine(t *testing.T, conn *fakeConnect, hsTimeout time.Duration) *Machine {
	t.Helper()
	return NewMachine(fakeAuth{user: "alice", pass: "pw"}, conn, Options{
		RequireAuth: true, HandshakeTimeout: hsTimeout,
	})
}

func successConnect() *fakeConnect {
	_, cli := net.Pipe()
	return &fakeConnect{result: &DialResult{
		Conn:  cli,
		Bound: wire.Target{ATyp: wire.ATypIPv4, Addr: netip.MustParseAddr("127.0.0.1"), Port: 8080},
	}}
}

func TestMethodNoAcceptable(t *testing.T) {
	fc := successConnect()
	h := newMachineHarness(t, authMachine(t, fc, time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()

	c := h.dial()
	defer func() { _ = c.Close() }()
	// Client offers only NO-AUTH while server requires user/pass.
	_, _ = c.Write([]byte{5, 1, 0})
	resp := readN(t, c, 2)
	if resp[0] != 5 || resp[1] != wire.MethodNoAcceptable {
		t.Fatalf("method resp = %v, want 05 FF", resp)
	}
	// Exchange must be terminated: a further read yields EOF.
	if b, err := c.Read(make([]byte, 1)); err != io.EOF || b != 0 {
		t.Fatalf("expected close after FF, got n=%d err=%v", b, err)
	}
	out := <-done
	if out.Kind != KindNoAcceptableMethod || out.Stage != StageMethod {
		t.Fatalf("outcome=%s stage=%s", out.Kind, out.Stage)
	}
	if fc.called {
		t.Fatal("connecter must not run when negotiation fails")
	}
}

func TestSegmentedHandshakeAndFullFlow(t *testing.T) {
	fc := successConnect()
	h := newMachineHarness(t, authMachine(t, fc, 5*time.Second))
	done := make(chan struct {
		o  *SessionOutcome
		up net.Conn
	}, 1)
	go func() {
		o, up := h.run()
		done <- struct {
			o  *SessionOutcome
			up net.Conn
		}{o, up}
	}()

	c := h.dial()
	defer func() { _ = c.Close() }()

	// 1) Method greeting, one byte at a time.
	segmented(t, c, []byte{5, 1, 2})
	if got := readN(t, c, 2); string(got) != "\x05\x02" {
		t.Fatalf("method select = %v", got)
	}

	// 2) Auth frame split across segments.
	segmented(t, c, []byte{1, 5, 'a', 'l', 'i', 'c', 'e', 2, 'p', 'w'})
	if got := readN(t, c, 2); string(got) != "\x01\x00" {
		t.Fatalf("auth status = %v", got)
	}

	// 3) CONNECT 127.0.0.1:8080, byte by byte.
	req := []byte{5, 1, 0, 1, 127, 0, 0, 1, 0x1f, 0x90}
	segmented(t, c, req)
	rep := readN(t, c, 10)
	if rep[0] != 5 || rep[1] != wire.RepSucceeded || rep[3] != wire.ATypIPv4 {
		t.Fatalf("success reply = %v", rep)
	}
	r := <-done
	if r.o.Kind != KindRelayReady {
		t.Fatalf("kind=%s", r.o.Kind)
	}
	if r.o.User != "alice" || r.up == nil {
		t.Fatalf("user=%q upstream=%v", r.o.User, r.up)
	}
	_ = r.up.Close()
}

func TestAuthRejected(t *testing.T) {
	fc := successConnect()
	h := newMachineHarness(t, authMachine(t, fc, time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()

	c := h.dial()
	defer func() { _ = c.Close() }()
	_, _ = c.Write([]byte{5, 1, 2})
	readN(t, c, 2)
	_, _ = c.Write([]byte{1, 5, 'a', 'l', 'i', 'c', 'e', 5, 'w', 'r', 'o', 'n', 'g'})
	if got := readN(t, c, 2); string(got) != "\x01\x01" {
		t.Fatalf("auth status = %v, want 01 01", got)
	}
	if b, err := c.Read(make([]byte, 1)); err != io.EOF || b != 0 {
		t.Fatalf("expected close after auth failure")
	}
	out := <-done
	if out.Kind != KindAuthDenied {
		t.Fatalf("kind=%s want auth_denied", out.Kind)
	}
	if fc.called {
		t.Fatal("connecter must not run after auth denial")
	}
}

func TestAuthBackendError(t *testing.T) {
	mac := NewMachine(fakeAuth{err: errors.New("sqlite is down")}, successConnect(),
		Options{RequireAuth: true, HandshakeTimeout: time.Second})
	h := newMachineHarness(t, mac)
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()
	c := h.dial()
	defer func() { _ = c.Close() }()
	_, _ = c.Write([]byte{5, 1, 2})
	readN(t, c, 2)
	_, _ = c.Write([]byte{1, 1, 'a', 1, 'b'})
	readN(t, c, 2)
	out := <-done
	if out.Kind != KindAuthError {
		t.Fatalf("kind=%s want auth_backend_error", out.Kind)
	}
}

func TestBadVersion(t *testing.T) {
	h := newMachineHarness(t, authMachine(t, successConnect(), time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()
	c := h.dial()
	defer func() { _ = c.Close() }()
	_, _ = c.Write([]byte{4, 1, 0})
	// Not SOCKS5: server closes without a method-selection reply.
	buf := make([]byte, 2)
	if _, err := io.ReadFull(c, buf); err == nil {
		t.Fatalf("server sent bytes %v to a non-socks5 client", buf)
	}
	out := <-done
	if out.Kind != KindProtocolVersion {
		t.Fatalf("kind=%s", out.Kind)
	}
}

func TestClientClosedEarly(t *testing.T) {
	h := newMachineHarness(t, authMachine(t, successConnect(), time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()
	c := h.dial()
	_, _ = c.Write([]byte{5}) // one byte only, then clean EOF
	_ = c.Close()
	out := <-done
	if out.Kind != KindMalformedFrame && out.Kind != KindClientClosed {
		t.Fatalf("kind=%s want client_closed or malformed truncated", out.Kind)
	}
}

func TestHandshakeDeadline(t *testing.T) {
	h := newMachineHarness(t, authMachine(t, successConnect(), 80*time.Millisecond))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()
	c := h.dial()
	defer func() { _ = c.Close() }()
	// Send nothing; deadline must fire.
	select {
	case out := <-done:
		if out.Kind != KindHandshakeDeadline {
			t.Fatalf("kind=%s want handshake_deadline", out.Kind)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("handshake deadline did not fire")
	}
}

func TestUnsupportedCommand(t *testing.T) {
	fc := successConnect()
	h := newMachineHarness(t, authMachine(t, fc, time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, up := h.run(); done <- o; _ = up }()
	c := h.dial()
	defer func() { _ = c.Close() }()
	_, _ = c.Write([]byte{5, 1, 2})
	readN(t, c, 2)
	_, _ = c.Write([]byte{1, 5, 'a', 'l', 'i', 'c', 'e', 2, 'p', 'w'})
	readN(t, c, 2)
	// CMD=2 (BIND)
	_, _ = c.Write([]byte{5, 2, 0, 1, 127, 0, 0, 1, 0, 80})
	rep := readN(t, c, 10)
	if rep[1] != wire.RepCommandNotSupported {
		t.Fatalf("rep=%d want command-not-supported", rep[1])
	}
	out := <-done
	if out.Kind != KindUnsupportedCommand || fc.called {
		t.Fatalf("kind=%s connecterCalled=%v", out.Kind, fc.called)
	}
}

func TestPolicyDeniedReply(t *testing.T) {
	fc := &fakeConnect{fail: NewFailure(KindPolicyDenied, "ip 8.8.8.8 matched no cidr rule")}
	h := newMachineHarness(t, authMachine(t, fc, time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()
	c := h.dial()
	defer func() { _ = c.Close() }()
	_, _ = c.Write([]byte{5, 1, 2})
	readN(t, c, 2)
	_, _ = c.Write([]byte{1, 5, 'a', 'l', 'i', 'c', 'e', 2, 'p', 'w'})
	readN(t, c, 2)
	_, _ = c.Write([]byte{5, 1, 0, 1, 8, 8, 8, 8, 0, 53})
	rep := readN(t, c, 10)
	if rep[1] != wire.RepConnectionNotAllowed {
		t.Fatalf("rep=%d want 0x02 connection-not-allowed", rep[1])
	}
	out := <-done
	if out.Kind != KindPolicyDenied {
		t.Fatalf("kind=%s", out.Kind)
	}
}

func TestMalformedRequestTruncated(t *testing.T) {
	fc := successConnect()
	h := newMachineHarness(t, authMachine(t, fc, time.Second))
	done := make(chan *SessionOutcome, 1)
	go func() { o, _ := h.run(); done <- o }()
	c := h.dial()
	defer func() { _ = c.Close() }()
	_, _ = c.Write([]byte{5, 1, 2})
	readN(t, c, 2)
	_, _ = c.Write([]byte{1, 5, 'a', 'l', 'i', 'c', 'e', 2, 'p', 'w'})
	readN(t, c, 2)
	// ATYP=1 claims an IPv4 address but only 2 address bytes follow.
	_, _ = c.Write([]byte{5, 1, 0, 1, 1, 2})
	_ = c.Close()
	out := <-done
	if out.Kind != KindMalformedFrame && out.Kind != KindClientClosed {
		t.Fatalf("kind=%s", out.Kind)
	}
	if fc.called {
		t.Fatal("connecter must not run on malformed request")
	}
}
