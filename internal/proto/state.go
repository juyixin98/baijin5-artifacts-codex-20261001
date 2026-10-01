// Package proto implements the SOCKS5 server-side state machine: method
// negotiation, optional username/password authentication, the CONNECT
// request, policy evaluation, vetted-address dialing and the reply.
//
// The package is transport-agnostic: it works against a net.Conn and two
// small injected interfaces (Authenticator, UpstreamDialer). It never
// re-resolves a domain itself; it dials only the exact addresses the policy
// vetted. Byte forwarding after a successful handshake lives in the server
// package so the half-close and budget logic is independently testable.
package proto

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"time"

	"socks5d.local/socks5d/internal/policy"
)

// Outcome classifies how the handshake ended, for tests and audit logging.
type Outcome string

const (
	OutcomeEstablished        Outcome = "established"
	OutcomeNoAcceptableMethod Outcome = "no_acceptable_method"
	OutcomeAuthFailed         Outcome = "auth_failed"
	OutcomeRejected           Outcome = "rejected" // a SOCKS5 REP reply other than success was sent
	OutcomeProtocolError      Outcome = "protocol_error"
	OutcomeDialFailed         Outcome = "dial_failed"
)

// TerminalError carries the machine-readable outcome and reason for a
// handshake that did not produce a relay. Whether a reply was sent to the
// client is recorded explicitly so callers never have to guess.
type TerminalError struct {
	Outcome   Outcome
	Reason    string // codec.Reason / policy reason / "..."
	Detail    string
	ReplySent bool
	Rep       byte // meaningful when ReplySent
}

func (e *TerminalError) Error() string {
	return fmt.Sprintf("socks5 handshake: %s: %s: %s", e.Outcome, e.Reason, e.Detail)
}

// IsTerminal reports whether err is a *TerminalError and returns it.
func IsTerminal(err error) (*TerminalError, bool) {
	var te *TerminalError
	if errors.As(err, &te) {
		return te, true
	}
	return nil, false
}

// Authenticator validates RFC 1929 credentials. A nil authenticator means
// the server offers the no-authentication method.
type Authenticator interface {
	Authenticate(username, password string) bool
}

// UpstreamDialer opens a connection to a vetted address.
type UpstreamDialer interface {
	DialContext(ctx context.Context, target netip.AddrPort) (net.Conn, error)
}

// Timeouts groups the handshake phases.
type Timeouts struct {
	Method time.Duration // whole greeting
	Auth   time.Duration // credential sub-negotiation
	Req    time.Duration // request frame
	Dial   time.Duration // upstream connect
}

// Established is a fully handshaken session ready for byte relay.
type Established struct {
	Upstream net.Conn
	Target   netip.AddrPort
	Rep      byte
}

// Options parameterizes the state machine.
type Options struct {
	Auth     Authenticator
	Policy   *policy.Policy
	Resolver policy.Resolver
	Dialer   UpstreamDialer
	Timeouts Timeouts
}

// stepConn scopes one deadline to a handshake phase, restoring it afterwards.
func withPhase(conn net.Conn, d time.Duration, fn func() error) error {
	if d > 0 {
		_ = conn.SetDeadline(time.Now().Add(d))
		defer func() { _ = conn.SetDeadline(time.Time{}) }()
	}
	return fn()
}
