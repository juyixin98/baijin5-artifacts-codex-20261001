// Package policy enforces the local-whitelist destination policy.
//
// The security rule applied here is deliberately fail-closed:
//
//   - An IP literal is allowed only if it falls inside a whitelisted CIDR.
//   - A domain is allowed only if its name matches a whitelisted domain rule
//     AND every resolved address falls inside a whitelisted CIDR. A single
//     resolved address outside the whitelist rejects the whole request, so a
//     permitted name can never be used to reach a public address.
//
// The concrete, already-vetted addresses are returned to the caller. The
// server must dial one of those exact AddrPorts and must never re-resolve the
// name: that keeps "the address policy decided" and "the address connected
// to" identical, eliminating a check/use gap.
package policy

import (
	"context"
	"fmt"
	"net"
	"net/netip"
	"strings"

	"socks5d.local/socks5d/internal/codec"
	"socks5d.local/socks5d/internal/config"
)

// Stable reason codes surfaced in replies, logs and audit records.
const (
	ReasonPortZero                   = "port_zero"
	ReasonIPNotWhitelisted           = "ip_not_whitelisted"
	ReasonDomainNotWhitelisted       = "domain_not_whitelisted"
	ReasonDNSFailed                  = "dns_lookup_failed"
	ReasonDNSNoRecords               = "dns_no_records"
	ReasonResolvedAddrNotWhitelisted = "resolved_addr_not_whitelisted"
)

// Resolver looks up a name. net.Resolver satisfies it; tests inject a static
// table for fully offline, deterministic multi-address results.
type Resolver interface {
	LookupNetIP(ctx context.Context, network, host string) ([]netip.Addr, error)
}

// StaticResolver resolves names from an in-memory table first and delegates
// misses to the wrapped platform resolver (which may be nil in tests).
type StaticResolver struct {
	Hosts    map[string][]netip.Addr
	Fallback Resolver
}

// LookupNetIP implements Resolver.
func (s StaticResolver) LookupNetIP(ctx context.Context, network, host string) ([]netip.Addr, error) {
	key := strings.TrimSuffix(strings.ToLower(host), ".")
	if addrs, ok := s.Hosts[key]; ok && len(addrs) > 0 {
		return append([]netip.Addr(nil), addrs...), nil
	}
	if s.Fallback != nil {
		return s.Fallback.LookupNetIP(ctx, network, host)
	}
	return nil, &net.DNSError{Err: "no static record and fallback resolver disabled", Name: host, IsNotFound: true}
}

// Decision is the result of evaluating one destination.
type Decision struct {
	Allowed bool
	Reason  string
	Detail  string
	// Targets are the vetted dial candidates (already policy-checked).
	Targets []netip.AddrPort
	// Matched identifies the rule that permitted the name/CIDR.
	Matched string
}

// Policy holds the compiled whitelist.
type Policy struct {
	cidrs    []netip.Prefix
	cidrsRaw []string
	exact    map[string]string // name -> rule note
	suffixes []suffixRule
}

type suffixRule struct {
	suffix string // leading dot form, e.g. ".internal"
	note   string
}

// FromRules compiles validated rules into an evaluable Policy.
func FromRules(rules []config.Rule) (*Policy, error) {
	p := &Policy{exact: map[string]string{}}
	for _, r := range rules {
		switch r.Kind {
		case "cidr":
			prefix, err := netip.ParsePrefix(r.Value)
			if err != nil {
				return nil, fmt.Errorf("compile cidr %q: %w", r.Value, err)
			}
			p.cidrs = append(p.cidrs, prefix.Masked())
			p.cidrsRaw = append(p.cidrsRaw, r.Value)
		case "domain":
			switch r.Mode {
			case "exact":
				p.exact[r.Value] = r.Note
			case "suffix":
				suf := r.Value
				if !strings.HasPrefix(suf, ".") {
					suf = "." + suf
				}
				p.suffixes = append(p.suffixes, suffixRule{suffix: suf, note: r.Note})
			}
		}
	}
	return p, nil
}

func (p *Policy) ipAllowed(addr netip.Addr) bool {
	for _, prefix := range p.cidrs {
		if prefix.Contains(addr) {
			return true
		}
	}
	return false
}

func (p *Policy) domainAllowed(name string) (bool, string) {
	if note, ok := p.exact[name]; ok {
		return true, "domain:exact:" + name + noteLabel(note)
	}
	for _, s := range p.suffixes {
		if strings.HasSuffix(name, s.suffix) {
			return true, "domain:suffix:" + s.suffix + noteLabel(s.note)
		}
	}
	return false, ""
}

func noteLabel(note string) string {
	if note == "" {
		return ""
	}
	return "(" + note + ")"
}

// Evaluate applies the policy to a parsed request destination and, for a
// domain, resolves and vets every candidate address.
func (p *Policy) Evaluate(ctx context.Context, dest codec.Addr, resolver Resolver) Decision {
	if dest.Port == 0 {
		return Decision{Allowed: false, Reason: ReasonPortZero, Detail: "port 0 is not a connectable target"}
	}

	switch dest.Atyp {
	case codec.AtypIPv4, codec.AtypIPv6:
		if !p.ipAllowed(dest.IP) {
			return Decision{Allowed: false, Reason: ReasonIPNotWhitelisted,
				Detail: fmt.Sprintf("address %s is not inside any whitelisted CIDR", dest.IP)}
		}
		target := netip.AddrPortFrom(dest.IP, dest.Port)
		return Decision{Allowed: true, Targets: []netip.AddrPort{target}, Matched: "cidr"}

	case codec.AtypDomain:
		name := strings.TrimSuffix(strings.ToLower(dest.Domain), ".")
		ok, matched := p.domainAllowed(name)
		if !ok {
			return Decision{Allowed: false, Reason: ReasonDomainNotWhitelisted,
				Detail: fmt.Sprintf("domain %q matches no whitelist rule", name)}
		}
		addrs, err := resolver.LookupNetIP(ctx, "ip", name)
		if err != nil {
			return Decision{Allowed: false, Reason: ReasonDNSFailed,
				Detail: fmt.Sprintf("resolve %q: %v", name, err)}
		}
		if len(addrs) == 0 {
			return Decision{Allowed: false, Reason: ReasonDNSNoRecords,
				Detail: fmt.Sprintf("resolve %q returned no addresses", name)}
		}
		for _, addr := range addrs {
			if !p.ipAllowed(addr.Unmap()) {
				return Decision{Allowed: false, Reason: ReasonResolvedAddrNotWhitelisted,
					Detail: fmt.Sprintf("domain %q resolved to %s which is outside whitelisted CIDRs", name, addr)}
			}
		}
		targets := make([]netip.AddrPort, 0, len(addrs))
		for _, addr := range addrs {
			targets = append(targets, netip.AddrPortFrom(addr.Unmap(), dest.Port))
		}
		return Decision{Allowed: true, Targets: targets, Matched: matched}

	default:
		return Decision{Allowed: false, Reason: ReasonIPNotWhitelisted,
			Detail: fmt.Sprintf("unsupported address type 0x%02x", dest.Atyp)}
	}
}

// NewStaticResolver builds the configured offline-first resolver.
func NewStaticResolver(hosts map[string][]string) (StaticResolver, error) {
	table := map[string][]netip.Addr{}
	for name, ips := range hosts {
		key := strings.TrimSuffix(strings.ToLower(name), ".")
		for _, raw := range ips {
			addr, err := netip.ParseAddr(raw)
			if err != nil {
				return StaticResolver{}, fmt.Errorf("resolver host %q: %w", name, err)
			}
			table[key] = append(table[key], addr)
		}
	}
	return StaticResolver{
		Hosts:    table,
		Fallback: net.DefaultResolver,
	}, nil
}
