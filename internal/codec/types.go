// Package codec implements byte-level SOCKS5 framing (RFC 1928) and the
// username/password sub-negotiation (RFC 1929).
//
// It contains no connection policy and no I/O scheduling: it only turns a
// byte stream into validated frames and frames back into bytes. Every decode
// failure is reported through a *DecodeError carrying a stable Reason code,
// so upper layers (and independent tests) can assert the exact failure
// category instead of matching on free-text errors.
package codec

import (
	"errors"
	"fmt"
	"net/netip"
)

// Protocol constants from RFC 1928 / RFC 1929.
const (
	Version5        byte = 0x05
	UserPassVersion byte = 0x01
	RSV             byte = 0x00
	MethodNoAuth    byte = 0x00
	MethodUserPass  byte = 0x02
	MethodNoAccept  byte = 0xFF // RFC 1928: no acceptable methods
	CmdConnect      byte = 0x01
	AtypIPv4        byte = 0x01
	AtypDomain      byte = 0x03
	AtypIPv6        byte = 0x04
	DomainMaxLen         = 255
	IPv4AddrLen          = 4
	IPv6AddrLen          = 16
	PortLen              = 2
)

// Reply field values (REP).
const (
	RepSucceeded               byte = 0x00
	RepGeneralFailure          byte = 0x01
	RepNotAllowedByRuleset     byte = 0x02
	RepNetworkUnreachable      byte = 0x03
	RepHostUnreachable         byte = 0x04
	RepConnectionRefused       byte = 0x05
	RepTTLExpired              byte = 0x06
	RepCommandNotSupported     byte = 0x07
	RepAddressTypeNotSupported byte = 0x08
)

// Username/password STATUS values.
const (
	UserPassSuccess byte = 0x00
	UserPassFailure byte = 0x01
)

// Reason is a stable, machine-readable failure category.
type Reason string

const (
	ReasonTruncated          Reason = "frame_truncated"
	ReasonUnsupportedVersion Reason = "unsupported_version"
	ReasonNoMethods          Reason = "no_methods_offered"
	ReasonBadUserPassVersion Reason = "bad_userpass_version"
	ReasonBadReserved        Reason = "bad_reserved_byte"
	ReasonUnsupportedCommand Reason = "unsupported_command"
	ReasonUnsupportedAtyp    Reason = "unsupported_atyp"
	ReasonIPv4Length         Reason = "ipv4_length"
	ReasonIPv6Length         Reason = "ipv6_length"
	ReasonDomainEmpty        Reason = "domain_empty"
	ReasonDomainTooLong      Reason = "domain_too_long"
)

// DecodeError is returned for every malformed frame.
type DecodeError struct {
	Stage  string // "method_negotiation" | "userpass" | "request" | "address"
	Reason Reason
	Err    error
}

func (e *DecodeError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("socks5 codec: %s: %s: %v", e.Stage, e.Reason, e.Err)
	}
	return fmt.Sprintf("socks5 codec: %s: %s", e.Stage, e.Reason)
}

func (e *DecodeError) Unwrap() error { return e.Err }

// IsDecodeError reports whether err is a *DecodeError and returns it.
func IsDecodeError(err error) (*DecodeError, bool) {
	var de *DecodeError
	if errors.As(err, &de) {
		return de, true
	}
	return nil, false
}

// Addr is a SOCKS5 destination or bound address.
//
// Exactly one of IP (AtypIPv4/AtypIPv6) or Domain (AtypDomain) is meaningful.
// The zero value is not a valid address.
type Addr struct {
	Atyp   byte
	IP     netip.Addr
	Domain string
	Port   uint16
}

// String renders host:port for logs.
func (a Addr) String() string {
	switch a.Atyp {
	case AtypDomain:
		return fmt.Sprintf("%s:%d", a.Domain, a.Port)
	default:
		return netip.AddrPortFrom(a.IP, a.Port).String()
	}
}

// MethodRequest is the parsed client greeting.
type MethodRequest struct {
	Methods []byte // offered authentication methods
}

// Supports reports whether method m was offered.
func (r MethodRequest) Supports(m byte) bool {
	for _, offered := range r.Methods {
		if offered == m {
			return true
		}
	}
	return false
}

// UserPassRequest is a parsed RFC 1929 credential pair.
type UserPassRequest struct {
	Username string
	Password string
}

// Request is a parsed SOCKS5 request. Only CONNECT is accepted by the
// state machine; Cmd is retained so that layer can emit the precise reply.
type Request struct {
	Cmd  byte
	Dest Addr
}
