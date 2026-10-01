// Package gateway binds together name resolution, the whitelist policy and
// the actual TCP dial. Its invariant is resolution/policy consistency:
//
//   - A domain request passes only if the name matches a domain rule AND
//     every address the resolver returned is covered by a cidr rule.
//   - The addresses actually dialed are exactly those checked addresses, in
//     resolver order; the dialer never performs a second, unchecked lookup
//     (dials use IP literals, never hostnames).
//
// This removes the classic DNS-rebinding gap between "what policy approved"
// and "what got connected".
package gateway

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"strings"
	"time"

	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/proto"
	"sockswhitelist/internal/wire"
)

// Resolver performs name lookups. net.Resolver satisfies it; tests inject a
// synthetic resolver that returns fixed, multi-address results.
type Resolver interface {
	LookupNetIP(ctx context.Context, network, host string) ([]netip.Addr, error)
}

// Dialer establishes a TCP connection to an IP literal. *net.Dialer
// satisfies it. Taking an interface lets tests inject refusal/timeout.
type Dialer interface {
	DialContext(ctx context.Context, network, address string) (net.Conn, error)
}

// Connector implements proto.Connecter.
type Connector struct {
	Store    *policy.Store
	Resolver Resolver
	Dialer   Dialer
	// ResolveTimeout bounds name resolution.
	ResolveTimeout time.Duration
	// DialTimeout bounds each individual address attempt.
	DialTimeout time.Duration
}

// NewConnector wires a production net.Resolver and net.Dialer.
func NewConnector(store *policy.Store, resolveTimeout, dialTimeout time.Duration) *Connector {
	d := &net.Dialer{Timeout: dialTimeout, KeepAlive: 30 * time.Second}
	return &Connector{
		Store:          store,
		Resolver:       d.Resolver,
		Dialer:         d,
		ResolveTimeout: resolveTimeout,
		DialTimeout:    dialTimeout,
	}
}

// Connect validates, resolves and dials. See package documentation for the
// consistency invariant.
func (c *Connector) Connect(ctx context.Context, target wire.Target) (*proto.DialResult, *proto.Failure) {
	port := int(target.Port)

	var addrs []netip.Addr
	nameRule := ""
	switch target.ATyp {
	case wire.ATypIPv4, wire.ATypIPv6:
		ip := target.Addr.Unmap()
		// Recheck address length/shape: wire already enforced 4/16 bytes,
		// but an IPv4-mapped IPv6 presented as ATYP=4 is normalized here.
		if !ip.IsValid() {
			return nil, proto.NewFailure(proto.KindPolicyDenied, "invalid ip literal")
		}
		d := c.Store.CheckIP(ip, port)
		if !d.Allowed {
			return nil, proto.NewFailure(proto.KindPolicyDenied, "%s", d.Reason)
		}
		addrs = []netip.Addr{ip}
	case wire.ATypDomain:
		dName := c.Store.CheckDomain(target.Domain, port)
		if !dName.Allowed {
			return nil, proto.NewFailure(proto.KindPolicyDenied, "%s", dName.Reason)
		}
		if dName.Matched != nil {
			nameRule = dName.Matched.Host
		}
		rctx, cancel := context.WithTimeout(ctx, c.ResolveTimeout)
		resolved, err := c.Resolver.LookupNetIP(rctx, "ip", strings.TrimSuffix(target.Domain, "."))
		cancel()
		if err != nil {
			return nil, classifyResolve(err, target.Domain)
		}
		addrs = normalizeAddrs(resolved)
		if len(addrs) == 0 {
			return nil, proto.NewFailure(proto.KindResolveFailed, "domain %q resolved to no addresses", target.Domain)
		}
		if uncovered, ok := c.Store.CheckIPAll(addrs, port); !ok {
			return nil, proto.NewFailure(proto.KindPolicyDenied,
				"domain %q matched rule %q but resolved address %s is outside every cidr rule (resolved=%v)",
				target.Domain, nameRule, uncovered, addrs)
		}
	default:
		return nil, proto.NewFailure(proto.KindAddressNotSupported, "ATYP=0x%02x", target.ATyp)
	}

	return c.dialSequential(ctx, addrs, port)
}

// dialSequential tries the policy-approved addresses in order. Only IP
// literals reach the dialer, so no second, unchecked DNS lookup can occur.
func (c *Connector) dialSequential(ctx context.Context, addrs []netip.Addr, port int) (*proto.DialResult, *proto.Failure) {
	res := &proto.DialResult{Resolved: addrs}
	seen := map[netip.Addr]bool{}
	var last *proto.Failure
	attemptClasses := map[proto.Kind]int{}

	for _, addr := range addrs {
		addr = addr.Unmap()
		if seen[addr] {
			continue
		}
		seen[addr] = true
		ap := netip.AddrPortFrom(addr, uint16(port))
		dctx, cancel := context.WithTimeout(ctx, c.DialTimeout)
		conn, err := c.Dialer.DialContext(dctx, "tcp", ap.String())
		cancel()
		if err == nil {
			res.Conn = conn
			res.Bound = boundTarget(conn)
			res.Attempts = append(res.Attempts, proto.DialAttempt{
				AddrPort: ap, Outcome: "connected",
			})
			return res, nil
		}
		kind := classifyDial(err)
		attemptClasses[kind]++
		res.Attempts = append(res.Attempts, proto.DialAttempt{
			AddrPort: ap, Outcome: string(kind), Err: err.Error(),
		})
		last = proto.NewFailure(kind, "dial %s: %v", ap, err)
	}

	if last == nil {
		last = proto.NewFailure(proto.KindDialFailed, "no addresses to dial")
	}
	// If every attempt failed for the same concrete reason, surface it;
	// otherwise report a general dial failure and keep per-attempt detail.
	if len(attemptClasses) == 1 {
		return nil, last
	}
	return nil, proto.NewFailure(proto.KindDialFailed, "all %d addresses failed; last: %v", len(res.Attempts), last)
}

func boundTarget(conn net.Conn) wire.Target {
	if la, ok := conn.LocalAddr().(*net.TCPAddr); ok {
		return wire.TargetFromAddrPort(la.AddrPort())
	}
	ap, err := netip.ParseAddrPort(conn.LocalAddr().String())
	if err != nil {
		return wire.Target{ATyp: wire.ATypIPv4}
	}
	return wire.TargetFromAddrPort(ap)
}

func normalizeAddrs(in []netip.Addr) []netip.Addr {
	out := make([]netip.Addr, 0, len(in))
	for _, a := range in {
		if a.IsValid() {
			out = append(out, a.Unmap())
		}
	}
	return out
}

func classifyResolve(err error, domain string) *proto.Failure {
	if errors.Is(err, context.DeadlineExceeded) {
		return proto.NewFailure(proto.KindResolveFailed, "resolve %q: timeout", domain)
	}
	// "no such host" style errors from the standard library are opaque;
	// treat a DNSError with IsNotFound as a plain failed resolution.
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		return proto.NewFailure(proto.KindResolveFailed, "resolve %q: %v", domain, dnsErr)
	}
	return proto.NewFailure(proto.KindResolveFailed, "resolve %q: %v", domain, err)
}

func classifyDial(err error) proto.Kind {
	if errors.Is(err, context.DeadlineExceeded) {
		return proto.KindDialTimeout
	}
	var nerr net.Error
	if errors.As(err, &nerr) && nerr.Timeout() {
		return proto.KindDialTimeout
	}
	msg := err.Error()
	switch {
	case strings.Contains(msg, "connection refused"):
		return proto.KindDialRefused
	case strings.Contains(msg, "network is unreachable"),
		strings.Contains(msg, "no route to host"),
		strings.Contains(msg, "host unreachable"):
		return proto.KindDialUnreachable
	default:
		return proto.KindDialFailed
	}
}

// Compile-time assertion that *net.Resolver satisfies Resolver.
var _ Resolver = (*net.Resolver)(nil)
