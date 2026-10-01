package policy

import (
	"context"
	"net/netip"
	"testing"
)

func newTestStore(t *testing.T) *Store {
	t.Helper()
	s, err := Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func TestCheckIPCIDR(t *testing.T) {
	s := newTestStore(t)
	err := s.ReplaceRules(context.Background(), []Rule{
		{Kind: KindCIDR, Host: "127.0.0.0/8", Ports: []int{8080, 9000}},
		{Kind: KindCIDR, Host: "::1/128", Ports: []int{0}},
		{Kind: KindCIDR, Host: "10.0.0.0/8", Ports: []int{443}},
	})
	if err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		name    string
		ip      string
		port    int
		allowed bool
	}{
		{"loopback allowed port", "127.0.0.1", 8080, true},
		{"loopback other allowed port", "127.1.2.3", 9000, true},
		{"loopback wrong port", "127.0.0.1", 22, false},
		{"outside all cidrs", "8.8.8.8", 8080, false},
		{"10/8 wrong port", "10.1.2.3", 8080, false},
		{"10/8 right port", "10.1.2.3", 443, true},
		{"ipv6 loopback any port", "::1", 12345, true},
		{"ipv6 global denied", "2001:4860:4860::8888", 12345, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := s.CheckIP(netip.MustParseAddr(tc.ip), tc.port)
			if d.Allowed != tc.allowed {
				t.Fatalf("CheckIP(%s,%d)=%v reason=%q, want allowed=%v", tc.ip, tc.port, d.Allowed, d.Reason, tc.allowed)
			}
		})
	}
}

func TestCheckDomainSuffix(t *testing.T) {
	s := newTestStore(t)
	err := s.ReplaceRules(context.Background(), []Rule{
		{Kind: KindDomain, Host: "localhost", Ports: []int{0}},
		{Kind: KindDomain, Host: "*.svc.test", Ports: []int{80}},
	})
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		host    string
		port    int
		allowed bool
	}{
		{"localhost", 65535, true},
		{"LocalHost.", 1, true}, // case + trailing dot normalize
		{"a.svc.test", 80, true},
		{"a.b.svc.test", 80, false}, // suffix only allows one label
		{"svc.test", 80, false},     // suffix does not match apex
		{"evilsvc.test", 80, false}, // label-boundary attack
		{"notlocalhost", 80, false},
		{"a.svc.test", 81, false}, // port
	}
	for _, tc := range cases {
		d := s.CheckDomain(tc.host, tc.port)
		if d.Allowed != tc.allowed {
			t.Fatalf("CheckDomain(%q,%d)=%v reason=%q, want %v", tc.host, tc.port, d.Allowed, d.Reason, tc.allowed)
		}
	}
}

func TestValidateRule(t *testing.T) {
	bad := []Rule{
		{Kind: KindDomain, Host: ""},
		{Kind: KindDomain, Host: "bad host"},
		{Kind: KindDomain, Host: "-leading.test"},
		{Kind: KindDomain, Host: "label-that-is-way-way-way-way-way-way-way-way-way-way-way-way-way-way-way-way-too-long.test"},
		{Kind: KindCIDR, Host: "999.1.1.1/8"},
		{Kind: "weird", Host: "x"},
		{Kind: KindCIDR, Host: "127.0.0.0/8", Ports: []int{70000}},
	}
	for i, r := range bad {
		if _, err := ValidateRule(r); err == nil {
			t.Fatalf("bad rule %d accepted: %+v", i, r)
		}
	}
	// Bare IP becomes host route.
	got, err := ValidateRule(Rule{Kind: KindCIDR, Host: "127.0.0.1"})
	if err != nil || got.Host != "127.0.0.1/32" {
		t.Fatalf("bare ip: %+v err=%v", got, err)
	}
	got, err = ValidateRule(Rule{Kind: KindCIDR, Host: "::1"})
	if err != nil || got.Host != "::1/128" {
		t.Fatalf("bare ipv6: %+v err=%v", got, err)
	}
}

func TestUserAuthRoundTrip(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	if err := s.SetUser(ctx, "alice", "s3cret"); err != nil {
		t.Fatal(err)
	}
	ok, err := s.Authenticate(ctx, "alice", "s3cret")
	if err != nil || !ok {
		t.Fatalf("correct creds: ok=%v err=%v", ok, err)
	}
	ok, err = s.Authenticate(ctx, "alice", "wrong")
	if err != nil || ok {
		t.Fatalf("wrong password: ok=%v err=%v", ok, err)
	}
	ok, err = s.Authenticate(ctx, "ghost", "s3cret")
	if err != nil || ok {
		t.Fatalf("unknown user: ok=%v err=%v", ok, err)
	}
	if err := s.DisableUser(ctx, "alice"); err != nil {
		t.Fatal(err)
	}
	ok, err = s.Authenticate(ctx, "alice", "s3cret")
	if err != nil || ok {
		t.Fatalf("disabled user: ok=%v err=%v", ok, err)
	}
}

func TestRequestLogRoundTrip(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	rec := RequestRecord{
		RequestID: "req_test1", ClientAddr: "127.0.0.1:9", Username: "alice",
		Stage: "relay", TargetHost: "localhost", TargetPort: 8080,
		Resolved:    []netip.Addr{netip.MustParseAddr("127.0.0.1")},
		OutcomeKind: "relay_completed", BytesUp: 11, BytesDown: 22,
	}
	id, err := s.LogRequest(ctx, rec)
	if err != nil {
		t.Fatal(err)
	}
	rec.BytesUp = 100
	if err := s.FinishRequest(ctx, id, rec); err != nil {
		t.Fatal(err)
	}
	rows, err := s.QueryRequests(ctx, "WHERE request_id=?", "req_test1")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows=%d", len(rows))
	}
	r := rows[0]
	if r.BytesUp != 100 || r.OutcomeKind != "relay_completed" || r.TargetHost != "localhost" {
		t.Fatalf("row not finalized: %+v", r)
	}
	if !r.FinishedAt.Valid {
		t.Fatal("finished_at not set")
	}
}
