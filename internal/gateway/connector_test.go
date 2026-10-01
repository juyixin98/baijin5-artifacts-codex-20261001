package gateway

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"sync"
	"testing"
	"time"

	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/proto"
	"sockswhitelist/internal/wire"
)

// fakeResolver returns scripted answers, recording queried hosts.
type fakeResolver struct {
	mu    sync.Mutex
	addrs map[string][]netip.Addr
	err   error
	calls []string
}

func (f *fakeResolver) LookupNetIP(_ context.Context, _, host string) ([]netip.Addr, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, host)
	if f.err != nil {
		return nil, f.err
	}
	return f.addrs[host], nil
}

func (f *fakeResolver) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

// fakeDialer records every address dialed and returns scripted errors.
type fakeDialer struct {
	mu        sync.Mutex
	dialed    []string
	failAddrs map[string]error
	conns     []net.Conn
}

func (d *fakeDialer) DialContext(_ context.Context, _, address string) (net.Conn, error) {
	d.mu.Lock()
	d.dialed = append(d.dialed, address)
	err := d.failAddrs[address]
	d.mu.Unlock()
	if err != nil {
		return nil, err
	}
	server, client := net.Pipe()
	d.mu.Lock()
	d.conns = append(d.conns, server)
	d.mu.Unlock()
	return client, nil
}

func (d *fakeDialer) dialedAddrs() []string {
	d.mu.Lock()
	defer d.mu.Unlock()
	out := make([]string, len(d.dialed))
	copy(out, d.dialed)
	return out
}

func newConnector(t *testing.T, rules []policy.Rule, res *fakeResolver, dial *fakeDialer) *Connector {
	t.Helper()
	store, err := policy.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.ReplaceRules(context.Background(), rules); err != nil {
		t.Fatal(err)
	}
	return &Connector{
		Store: store, Resolver: res, Dialer: dial,
		ResolveTimeout: time.Second, DialTimeout: time.Second,
	}
}

func localRules() []policy.Rule {
	return []policy.Rule{
		{Kind: policy.KindCIDR, Host: "127.0.0.0/8", Ports: []int{0}},
		{Kind: policy.KindCIDR, Host: "::1/128", Ports: []int{0}},
		{Kind: policy.KindDomain, Host: "*.local.test", Ports: []int{0}},
	}
}

func TestConnectIPAllowDeny(t *testing.T) {
	dial := &fakeDialer{}
	c := newConnector(t, localRules(), &fakeResolver{}, dial)

	// Allowed loopback.
	res, f := c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypIPv4, Addr: netip.MustParseAddr("127.0.0.1"), Port: 9999})
	if f != nil {
		t.Fatalf("allowed ip denied: %v", f)
	}
	if got := dial.dialedAddrs(); len(got) != 1 || got[0] != "127.0.0.1:9999" {
		t.Fatalf("dialed=%v", got)
	}
	_ = res.Conn.Close()

	// Denied external IP: dialer must never be touched.
	before := len(dial.dialedAddrs())
	_, f = c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypIPv4, Addr: netip.MustParseAddr("203.0.113.5"), Port: 9999})
	if f == nil || f.Kind != proto.KindPolicyDenied {
		t.Fatalf("external ip: f=%v", f)
	}
	if len(dial.dialedAddrs()) != before {
		t.Fatalf("dialer invoked for denied target: %v", dial.dialedAddrs())
	}
}

func TestConnectDomainMultiAddressFallback(t *testing.T) {
	res := &fakeResolver{addrs: map[string][]netip.Addr{
		"web.local.test": {
			netip.MustParseAddr("127.0.0.2"), // will refuse
			netip.MustParseAddr("127.0.0.3"), // will connect
		},
	}}
	dial := &fakeDialer{failAddrs: map[string]error{
		"127.0.0.2:80": errors.New("dial tcp 127.0.0.2:80: connect: connection refused"),
	}}
	c := newConnector(t, localRules(), res, dial)

	out, f := c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypDomain, Domain: "web.local.test", Port: 80})
	if f != nil {
		t.Fatalf("expected fallback success, got %v", f)
	}
	if got := dial.dialedAddrs(); len(got) != 2 || got[0] != "127.0.0.2:80" || got[1] != "127.0.0.3:80" {
		t.Fatalf("attempt order=%v", got)
	}
	if len(out.Attempts) != 2 || out.Attempts[0].Outcome != string(proto.KindDialRefused) ||
		out.Attempts[1].Outcome != "connected" {
		t.Fatalf("attempts=%+v", out.Attempts)
	}
	if len(out.Resolved) != 2 {
		t.Fatalf("resolved=%v", out.Resolved)
	}
	_ = out.Conn.Close()
}

func TestConnectDomainRebindBlocked(t *testing.T) {
	// Name matches the domain rule but resolves to a non-local address:
	// the address policy must veto and the dialer must not be called.
	res := &fakeResolver{addrs: map[string][]netip.Addr{
		"evil.local.test": {netip.MustParseAddr("198.51.100.9")},
	}}
	dial := &fakeDialer{}
	c := newConnector(t, localRules(), res, dial)

	_, f := c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypDomain, Domain: "evil.local.test", Port: 80})
	if f == nil || f.Kind != proto.KindPolicyDenied {
		t.Fatalf("rebind: f=%v", f)
	}
	if len(dial.dialedAddrs()) != 0 {
		t.Fatalf("dialer invoked for rebinding domain: %v", dial.dialedAddrs())
	}
}

func TestConnectDomainNotWhitelisted(t *testing.T) {
	res := &fakeResolver{}
	dial := &fakeDialer{}
	c := newConnector(t, localRules(), res, dial)
	_, f := c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypDomain, Domain: "www.example.com", Port: 80})
	if f == nil || f.Kind != proto.KindPolicyDenied {
		t.Fatalf("f=%v", f)
	}
	if res.callCount() != 0 {
		t.Fatalf("resolver must not run for a name with no domain rule, calls=%v", res.calls)
	}
}

func TestConnectDomainResolveFailure(t *testing.T) {
	res := &fakeResolver{err: errors.New("lookup: no such host")}
	dial := &fakeDialer{}
	c := newConnector(t, localRules(), res, dial)
	_, f := c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypDomain, Domain: "missing.local.test", Port: 80})
	if f == nil || f.Kind != proto.KindResolveFailed {
		t.Fatalf("f=%v", f)
	}
	if len(dial.dialedAddrs()) != 0 {
		t.Fatalf("dial invoked after resolve failure: %v", dial.dialedAddrs())
	}
}

func TestConnectAllAddressesRefused(t *testing.T) {
	res := &fakeResolver{addrs: map[string][]netip.Addr{
		"web.local.test": {netip.MustParseAddr("127.0.0.2"), netip.MustParseAddr("127.0.0.3")},
	}}
	dial := &fakeDialer{failAddrs: map[string]error{
		"127.0.0.2:80": errors.New("connect: connection refused"),
		"127.0.0.3:80": errors.New("connect: connection refused"),
	}}
	c := newConnector(t, localRules(), res, dial)
	_, f := c.Connect(context.Background(), wire.Target{
		ATyp: wire.ATypDomain, Domain: "web.local.test", Port: 80})
	if f == nil || f.Kind != proto.KindDialRefused {
		t.Fatalf("f=%v", f)
	}
	if got := dial.dialedAddrs(); len(got) != 2 {
		t.Fatalf("both addresses must be attempted, got %v", got)
	}
}
