package policy_test

import (
	"context"
	"net/netip"
	"testing"

	"socks5d.local/socks5d/internal/codec"
	"socks5d.local/socks5d/internal/config"
	"socks5d.local/socks5d/internal/policy"
)

type fakeResolver struct {
	addr map[string][]netip.Addr
	err  error
}

func (f fakeResolver) LookupNetIP(_ context.Context, _, host string) ([]netip.Addr, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.addr[host], nil
}

func mustPolicy(t *testing.T, rules ...config.Rule) *policy.Policy {
	t.Helper()
	p, err := policy.FromRules(rules)
	if err != nil {
		t.Fatalf("compile policy: %v", err)
	}
	return p
}

func loopbackPolicy(t *testing.T) *policy.Policy {
	return mustPolicy(t,
		config.Rule{Kind: "cidr", Value: "127.0.0.0/8"},
		config.Rule{Kind: "cidr", Value: "::1/128"},
		config.Rule{Kind: "domain", Value: "echo.local", Mode: "exact"},
		config.Rule{Kind: "domain", Value: "internal", Mode: "suffix"},
	)
}

func TestIPLiteral_WhitelistBoundary(t *testing.T) {
	p := loopbackPolicy(t)
	tests := []struct {
		name    string
		addr    codec.Addr
		allowed bool
		reason  string
	}{
		{"loopback_v4", codec.IPv4Addr("127.0.0.1", 8080), true, ""},
		{"loopback_v4_edge_127_255", codec.IPv4Addr("127.255.255.255", 80), true, ""},
		{"just_outside_128", codec.IPv4Addr("128.0.0.1", 80), false, policy.ReasonIPNotWhitelisted},
		{"public_address", codec.IPv4Addr("8.8.8.8", 53), false, policy.ReasonIPNotWhitelisted},
		{"private_10_not_whitelisted", codec.IPv4Addr("10.0.0.1", 80), false, policy.ReasonIPNotWhitelisted},
		{"loopback_v6", codec.IPv6Addr("::1", 80), true, ""},
		{"public_v6", codec.IPv6Addr("2606:4700:4700::1111", 443), false, policy.ReasonIPNotWhitelisted},
		{"zero_address_v4", codec.IPv4Addr("0.0.0.0", 80), false, policy.ReasonIPNotWhitelisted},
		{"port_zero", codec.IPv4Addr("127.0.0.1", 0), false, policy.ReasonPortZero},
	}
	ctx := context.Background()
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			d := p.Evaluate(ctx, tc.addr, fakeResolver{})
			if d.Allowed != tc.allowed {
				t.Fatalf("allowed = %v want %v (reason=%s detail=%s)",
					d.Allowed, tc.allowed, d.Reason, d.Detail)
			}
			if !tc.allowed && d.Reason != tc.reason {
				t.Fatalf("reason = %q want %q", d.Reason, tc.reason)
			}
		})
	}
}

// TestDomain_MultiAddress_AllMustBeLocal is the key anti-redirect property:
// a permitted name resolving to even one public address is rejected and no
// public candidate is returned for dialing.
func TestDomain_MultiAddress_AllMustBeLocal(t *testing.T) {
	p := loopbackPolicy(t)
	resolver := fakeResolver{addr: map[string][]netip.Addr{
		"echo.local": {
			netip.MustParseAddr("127.0.0.1"),
			netip.MustParseAddr("127.0.0.2"),
			netip.MustParseAddr("::1"),
		},
	}}
	d := p.Evaluate(context.Background(), codec.DomainAddr("echo.local", 9000), resolver)
	if !d.Allowed {
		t.Fatalf("all-local multi-address should be allowed: %s %s", d.Reason, d.Detail)
	}
	if len(d.Targets) != 3 {
		t.Fatalf("targets = %d want 3", len(d.Targets))
	}
	for _, tgt := range d.Targets {
		if tgt.Port() != 9000 {
			t.Fatalf("port lost: %v", tgt)
		}
	}
}

func TestDomain_OnePublicRecordRejectsAll(t *testing.T) {
	p := loopbackPolicy(t)
	resolver := fakeResolver{addr: map[string][]netip.Addr{
		"echo.local": {
			netip.MustParseAddr("127.0.0.1"),
			netip.MustParseAddr("93.184.216.34"), // public; must veto the whole request
		},
	}}
	d := p.Evaluate(context.Background(), codec.DomainAddr("echo.local", 9000), resolver)
	if d.Allowed {
		t.Fatal("must reject when any resolved address is outside the whitelist")
	}
	if d.Reason != policy.ReasonResolvedAddrNotWhitelisted {
		t.Fatalf("reason = %q want %q", d.Reason, policy.ReasonResolvedAddrNotWhitelisted)
	}
	if len(d.Targets) != 0 {
		t.Fatalf("no dial targets may be returned, got %v", d.Targets)
	}
}

func TestDomain_NotWhitelisted(t *testing.T) {
	p := loopbackPolicy(t)
	d := p.Evaluate(context.Background(), codec.DomainAddr("evil.example", 80),
		fakeResolver{addr: map[string][]netip.Addr{"evil.example": {netip.MustParseAddr("127.0.0.1")}}})
	if d.Allowed || d.Reason != policy.ReasonDomainNotWhitelisted {
		t.Fatalf("got allowed=%v reason=%s", d.Allowed, d.Reason)
	}
}

func TestDomain_SuffixAndExact(t *testing.T) {
	p := loopbackPolicy(t)
	resolver := fakeResolver{addr: map[string][]netip.Addr{
		"svc.internal": {netip.MustParseAddr("127.0.0.1")},
		"a.b.internal": {netip.MustParseAddr("127.0.0.1")},
		"notinternal":  {netip.MustParseAddr("127.0.0.1")},
		"x.internal":   {netip.MustParseAddr("127.0.0.1")},
		"echo.local":   {netip.MustParseAddr("127.0.0.1")},
		"echo.local.x": {netip.MustParseAddr("127.0.0.1")},
	}}
	cases := []struct {
		name string
		host string
		want bool
	}{
		{"suffix_hit", "svc.internal", true},
		{"suffix_hit_subdomain", "a.b.internal", true},
		{"suffix_miss_no_dot", "notinternal", false},
		{"exact_hit", "echo.local", true},
		{"exact_no_substring", "echo.local.x", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := p.Evaluate(context.Background(), codec.DomainAddr(tc.host, 80), resolver)
			if d.Allowed != tc.want {
				t.Fatalf("%s allowed=%v reason=%s", tc.host, d.Allowed, d.Reason)
			}
		})
	}
}

func TestDomain_DNSFailureCategories(t *testing.T) {
	p := loopbackPolicy(t)
	t.Run("lookup_error", func(t *testing.T) {
		d := p.Evaluate(context.Background(), codec.DomainAddr("echo.local", 80),
			fakeResolver{err: dnsErr("boom")})
		if d.Allowed || d.Reason != policy.ReasonDNSFailed {
			t.Fatalf("got allowed=%v reason=%s", d.Allowed, d.Reason)
		}
	})
	t.Run("no_records", func(t *testing.T) {
		d := p.Evaluate(context.Background(), codec.DomainAddr("echo.local", 80),
			fakeResolver{addr: map[string][]netip.Addr{}})
		if d.Allowed || d.Reason != policy.ReasonDNSNoRecords {
			t.Fatalf("got allowed=%v reason=%s", d.Allowed, d.Reason)
		}
	})
}

type dnsErr string

func (d dnsErr) Error() string   { return string(d) }
func (d dnsErr) Timeout() bool   { return false }
func (d dnsErr) Temporary() bool { return false }
