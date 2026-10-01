package proto_test

import (
	"context"
	"io"
	"net"
	"net/netip"
	"sync"
	"syscall"
	"testing"
	"time"

	"socks5d.local/socks5d/internal/codec"
	"socks5d.local/socks5d/internal/config"
	"socks5d.local/socks5d/internal/policy"
	"socks5d.local/socks5d/internal/proto"
)

// fakeResolver is a static, offline resolver.
type fakeResolver map[string][]netip.Addr

func (f fakeResolver) LookupNetIP(_ context.Context, _, host string) ([]netip.Addr, error) {
	if a, ok := f[host]; ok {
		return a, nil
	}
	return nil, &net.DNSError{Err: "not found", Name: host, IsNotFound: true}
}

// fakeDialer records every vetted address it was asked to dial and can fail
// selected ones. A successful dial yields an independent net.Pipe pair; the
// test may take the server end via upstreamEnd.
type fakeDialer struct {
	mu      sync.Mutex
	dialed  []netip.AddrPort
	fail    map[netip.AddrPort]error
	up      map[netip.AddrPort]net.Conn
	created []net.Conn // every connection the dialer handed out
}

func newFakeDialer() *fakeDialer {
	return &fakeDialer{fail: map[netip.AddrPort]error{}, up: map[netip.AddrPort]net.Conn{}}
}

func (d *fakeDialer) DialContext(_ context.Context, target netip.AddrPort) (net.Conn, error) {
	d.mu.Lock()
	d.dialed = append(d.dialed, target)
	err := d.fail[target]
	d.mu.Unlock()
	if err != nil {
		return nil, err
	}
	clientEnd, serverEnd := net.Pipe()
	d.mu.Lock()
	d.up[target] = serverEnd
	d.created = append(d.created, clientEnd, serverEnd)
	d.mu.Unlock()
	return clientEnd, nil
}

func (d *fakeDialer) dialedAddrs() []netip.AddrPort {
	d.mu.Lock()
	defer d.mu.Unlock()
	out := make([]netip.AddrPort, len(d.dialed))
	copy(out, d.dialed)
	return out
}

func (d *fakeDialer) closeAll() {
	d.mu.Lock()
	defer d.mu.Unlock()
	for _, c := range d.created {
		_ = c.Close()
	}
}

type staticAuth struct{ ok bool }

func (s *staticAuth) Authenticate(_, _ string) bool { return s.ok }

// harness owns both ends of the SOCKS5 transport: the test drives client and
// the server-side Handshake runs on srv.
type harness struct {
	client net.Conn
	srv    net.Conn
	dialer *fakeDialer
	opts   proto.Options
}

func newHarness(t *testing.T, authn proto.Authenticator, rules []config.Rule, resolver policy.Resolver) *harness {
	t.Helper()
	pol, err := policy.FromRules(rules)
	if err != nil {
		t.Fatalf("policy: %v", err)
	}
	client, srv := net.Pipe()
	d := newFakeDialer()
	h := &harness{client: client, srv: srv, dialer: d,
		opts: proto.Options{
			Auth:     authn,
			Policy:   pol,
			Resolver: resolver,
			Dialer:   d,
			Timeouts: proto.Timeouts{
				Method: 2 * time.Second, Auth: 2 * time.Second,
				Req: 2 * time.Second, Dial: 2 * time.Second,
			},
		},
	}
	t.Cleanup(func() {
		_ = client.Close()
		_ = srv.Close()
		d.closeAll()
	})
	return h
}

type hsResult struct {
	est *proto.Established
	err error
}

// run starts the server handshake and returns the result channel.
func (h *harness) run() <-chan hsResult {
	ch := make(chan hsResult, 1)
	go func() {
		est, err := proto.Handshake(context.Background(), h.srv, h.opts)
		ch <- hsResult{est, err}
	}()
	return ch
}

func readExact(t *testing.T, r io.Reader, n int) []byte {
	t.Helper()
	buf := make([]byte, n)
	if _, err := io.ReadFull(r, buf); err != nil {
		t.Fatalf("read %d bytes: %v", n, err)
	}
	return buf
}

func loopbackRules() []config.Rule {
	return []config.Rule{
		{Kind: "cidr", Value: "127.0.0.0/8"},
		{Kind: "cidr", Value: "::1/128"},
		{Kind: "domain", Value: "echo.local", Mode: "exact"},
	}
}

func TestHandshake_SuccessIPv4(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	if sel := readExact(t, h.client, 2); !equalBytes(sel, codec.EncodeMethodSelection(codec.MethodNoAuth)) {
		t.Fatalf("selection = %x", sel)
	}
	h.client.Write(codec.EncodeRequest(codec.CmdConnect, codec.IPv4Addr("127.0.0.1", 9000)))
	reply := readExact(t, h.client, 10)
	if reply[1] != codec.RepSucceeded {
		t.Fatalf("rep = 0x%02x want 0x00", reply[1])
	}

	r := <-res
	if r.err != nil {
		t.Fatalf("handshake: %v", r.err)
	}
	want := netip.MustParseAddrPort("127.0.0.1:9000")
	if r.est.Target != want {
		t.Fatalf("target = %v want %v", r.est.Target, want)
	}
	if dialed := h.dialer.dialedAddrs(); len(dialed) != 1 || dialed[0] != want {
		t.Fatalf("dialed = %v want [%v]", dialed, want)
	}
}

func TestHandshake_NoAcceptableMethod_NoAuthServer(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()

	// No-auth server; client offers only user/pass.
	h.client.Write(codec.EncodeMethods(codec.MethodUserPass))
	if sel := readExact(t, h.client, 2); !equalBytes(sel, codec.EncodeNoAcceptable()) {
		t.Fatalf("selection = %x want 05ff", sel)
	}
	r := <-res
	te, ok := proto.IsTerminal(r.err)
	if !ok || te.Outcome != proto.OutcomeNoAcceptableMethod || !te.ReplySent {
		t.Fatalf("err = %v want no_acceptable_method (reply sent)", r.err)
	}
}

func TestHandshake_AuthRequiredButNotOffered(t *testing.T) {
	h := newHarness(t, &staticAuth{ok: true}, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth)) // server requires 0x02
	if sel := readExact(t, h.client, 2); sel[1] != codec.MethodNoAccept {
		t.Fatalf("selection = %x want 05ff", sel)
	}
	r := <-res
	if te, ok := proto.IsTerminal(r.err); !ok || te.Outcome != proto.OutcomeNoAcceptableMethod {
		t.Fatalf("err = %v want no_acceptable_method", r.err)
	}
}

func TestHandshake_BadCredentials(t *testing.T) {
	h := newHarness(t, &staticAuth{ok: false}, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodUserPass))
	if sel := readExact(t, h.client, 2); sel[1] != codec.MethodUserPass {
		t.Fatalf("selection method = 0x%02x want 0x02", sel[1])
	}
	h.client.Write(codec.EncodeUserPass("u", "p"))
	if status := readExact(t, h.client, 2); status[1] != codec.UserPassFailure {
		t.Fatalf("status = 0x%02x want 0x01", status[1])
	}
	r := <-res
	if te, ok := proto.IsTerminal(r.err); !ok || te.Outcome != proto.OutcomeAuthFailed {
		t.Fatalf("err = %v want auth_failed", r.err)
	}
}

func TestHandshake_GoodCredentialsThenSuccess(t *testing.T) {
	h := newHarness(t, &staticAuth{ok: true}, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodUserPass))
	readExact(t, h.client, 2)
	h.client.Write(codec.EncodeUserPass("u", "p"))
	if status := readExact(t, h.client, 2); status[1] != codec.UserPassSuccess {
		t.Fatalf("status = 0x%02x want 0x00", status[1])
	}
	h.client.Write(codec.EncodeRequest(codec.CmdConnect, codec.IPv4Addr("127.0.0.1", 9000)))
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepSucceeded {
		t.Fatalf("rep = 0x%02x want 0x00", reply[1])
	}
	if r := <-res; r.err != nil {
		t.Fatalf("handshake: %v", r.err)
	}
}

func TestHandshake_PolicyReject_IPNotWhitelisted(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	readExact(t, h.client, 2)
	h.client.Write(codec.EncodeRequest(codec.CmdConnect, codec.IPv4Addr("8.8.8.8", 53)))
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepNotAllowedByRuleset {
		t.Fatalf("rep = 0x%02x want 0x02", reply[1])
	}
	r := <-res
	te, ok := proto.IsTerminal(r.err)
	if !ok || te.Outcome != proto.OutcomeRejected || te.Rep != codec.RepNotAllowedByRuleset {
		t.Fatalf("err = %v want rejected/0x02", r.err)
	}
	if len(h.dialer.dialedAddrs()) != 0 {
		t.Fatal("must not dial a policy-rejected target")
	}
}

func TestHandshake_DomainNotWhitelisted(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	readExact(t, h.client, 2)
	h.client.Write(codec.EncodeRequest(codec.CmdConnect, codec.DomainAddr("evil.example", 80)))
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepNotAllowedByRuleset {
		t.Fatalf("rep = 0x%02x want 0x02", reply[1])
	}
	r := <-res
	if te, ok := proto.IsTerminal(r.err); !ok || te.Reason != policy.ReasonDomainNotWhitelisted {
		t.Fatalf("err = %v want reason %s", r.err, policy.ReasonDomainNotWhitelisted)
	}
}

func TestHandshake_DialRefused_MapsToRep05(t *testing.T) {
	target := netip.MustParseAddrPort("127.0.0.1:9001")
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	h.dialer.fail[target] = syscall.ECONNREFUSED
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	readExact(t, h.client, 2)
	h.client.Write(codec.EncodeRequest(codec.CmdConnect, codec.IPv4Addr("127.0.0.1", 9001)))
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepConnectionRefused {
		t.Fatalf("rep = 0x%02x want 0x05", reply[1])
	}
	r := <-res
	if te, ok := proto.IsTerminal(r.err); !ok || te.Outcome != proto.OutcomeDialFailed {
		t.Fatalf("err = %v want dial_failed", r.err)
	}
}

func TestHandshake_DialsVettedCandidatesInOrder(t *testing.T) {
	resolver := fakeResolver{"echo.local": {
		netip.MustParseAddr("127.0.0.2"), // fails
		netip.MustParseAddr("127.0.0.1"), // succeeds
	}}
	h := newHarness(t, nil, loopbackRules(), resolver)
	h.dialer.fail[netip.MustParseAddrPort("127.0.0.2:9000")] = syscall.ECONNREFUSED
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	readExact(t, h.client, 2)
	h.client.Write(codec.EncodeRequest(codec.CmdConnect, codec.DomainAddr("echo.local", 9000)))
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepSucceeded {
		t.Fatalf("rep = 0x%02x want 0x00", reply[1])
	}
	r := <-res
	if r.err != nil {
		t.Fatalf("handshake: %v", r.err)
	}
	if r.est.Target != netip.MustParseAddrPort("127.0.0.1:9000") {
		t.Fatalf("connected to %v, want second vetted candidate", r.est.Target)
	}
	want := []netip.AddrPort{
		netip.MustParseAddrPort("127.0.0.2:9000"),
		netip.MustParseAddrPort("127.0.0.1:9000"),
	}
	got := h.dialer.dialedAddrs()
	if len(got) != 2 || got[0] != want[0] || got[1] != want[1] {
		t.Fatalf("dial order = %v want %v", got, want)
	}
}

func TestHandshake_UnsupportedCommand_Rep07(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	readExact(t, h.client, 2)
	h.client.Write(codec.EncodeRequest(0x02, codec.IPv4Addr("127.0.0.1", 9000))) // BIND
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepCommandNotSupported {
		t.Fatalf("rep = 0x%02x want 0x07", reply[1])
	}
	r := <-res
	if te, ok := proto.IsTerminal(r.err); !ok || te.Rep != codec.RepCommandNotSupported {
		t.Fatalf("err = %v want rep 0x07", r.err)
	}
}

func TestHandshake_UnsupportedAtyp_Rep08(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()

	h.client.Write(codec.EncodeMethods(codec.MethodNoAuth))
	readExact(t, h.client, 2)
	// On an unknown ATYP the server rejects after the 4-byte header without
	// consuming an address body, so send exactly four bytes.
	h.client.Write([]byte{0x05, 0x01, 0x00, 0x05})
	if reply := readExact(t, h.client, 10); reply[1] != codec.RepAddressTypeNotSupported {
		t.Fatalf("rep = 0x%02x want 0x08", reply[1])
	}
	<-res
}

func TestHandshake_ClientEOFBeforeGreeting(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()
	_ = h.client.Close()
	r := <-res
	te, ok := proto.IsTerminal(r.err)
	if !ok || te.Outcome != proto.OutcomeProtocolError {
		t.Fatalf("err = %v want protocol_error", r.err)
	}
	if te.ReplySent {
		t.Fatal("no reply should be sent on an EOF greeting")
	}
}

func TestHandshake_TruncatedGreeting_NoReply(t *testing.T) {
	h := newHarness(t, nil, loopbackRules(), fakeResolver{})
	res := h.run()
	// Declares one method but sends none, then closes.
	h.client.Write([]byte{0x05, 0x01})
	_ = h.client.Close()
	r := <-res
	te, ok := proto.IsTerminal(r.err)
	if !ok || te.Outcome != proto.OutcomeProtocolError {
		t.Fatalf("err = %v want protocol_error", r.err)
	}
	if te.ReplySent {
		t.Fatal("malformed frame must not receive a reply")
	}
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
