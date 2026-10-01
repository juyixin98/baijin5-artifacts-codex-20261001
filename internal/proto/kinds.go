package proto

import (
	"errors"
	"fmt"
)

// Stage identifies the SOCKS5 exchange phase a session reached.
type Stage string

const (
	StageMethod  Stage = "method_negotiation"
	StageAuth    Stage = "username_password"
	StageRequest Stage = "request"
	StageConnect Stage = "connect"
	StageRelay   Stage = "relay"
)

// Kind is the machine-classifiable outcome of a session. Independent tests
// assert on these concrete categories; they are also stored in the SQLite
// request log and emitted in structured logs.
type Kind string

const (
	// Handshake categories.
	KindClientClosed        Kind = "client_closed"        // peer EOF before any frame completed
	KindMalformedFrame      Kind = "malformed_frame"      // length/structure invalid
	KindProtocolVersion     Kind = "protocol_version"     // VER was not 0x05
	KindNoAcceptableMethod  Kind = "no_acceptable_method" // 0xFF was sent; exchange ended
	KindAuthMalformed       Kind = "auth_subnegotiation"  // RFC 1929 frame invalid
	KindAuthDenied          Kind = "auth_denied"          // credentials rejected
	KindAuthError           Kind = "auth_backend_error"   // authenticator failed
	KindHandshakeDeadline   Kind = "handshake_deadline"   // client too slow
	KindUnsupportedCommand  Kind = "unsupported_command"  // only CONNECT is implemented
	KindAddressNotSupported Kind = "address_type_unsupported"

	// Gateway categories.
	KindPolicyDenied    Kind = "policy_denied"  // not present in the local whitelist
	KindResolveFailed   Kind = "resolve_failed" // name lookup yielded nothing usable
	KindDialRefused     Kind = "dial_refused"
	KindDialUnreachable Kind = "dial_network_unreachable"
	KindDialTimeout     Kind = "dial_timeout"
	KindDialFailed      Kind = "dial_failed"

	// Relay categories.
	KindRelayEOF           Kind = "relay_completed"
	KindRelayPeerReset     Kind = "relay_peer_reset"
	KindByteBudgetExceeded Kind = "byte_budget_exceeded"
	KindIdleTimeout        Kind = "idle_timeout"
	KindRelayFailed        Kind = "relay_failed"

	KindInternal Kind = "internal_error"
)

// Failure carries a category together with the underlying error. Every error
// path in the state machine and gateway returns one so callers never have to
// guess the failure class from an OS error string.
type Failure struct {
	Kind Kind
	Err  error
}

func (f *Failure) Error() string {
	if f.Err == nil {
		return string(f.Kind)
	}
	return fmt.Sprintf("%s: %v", f.Kind, f.Err)
}

func (f *Failure) Unwrap() error { return f.Err }

// NewFailure builds a categorized failure.
func NewFailure(kind Kind, format string, args ...any) *Failure {
	return &Failure{Kind: kind, Err: fmt.Errorf(format, args...)}
}

// AsFailure extracts a *Failure from err, wrapping unclassified errors as
// KindInternal.
func AsFailure(err error) *Failure {
	if err == nil {
		return nil
	}
	var f *Failure
	if errors.As(err, &f) {
		return f
	}
	return &Failure{Kind: KindInternal, Err: err}
}
