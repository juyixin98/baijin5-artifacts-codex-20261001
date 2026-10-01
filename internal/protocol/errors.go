package protocol

import "fmt"

// FailureKind classifies a protocol failure so tests can assert the concrete
// reason rather than merely that "a call returned an error".
type FailureKind int

// Failure categories.
const (
	KindUnknown FailureKind = iota
	// KindTimeout: all retransmissions were exhausted without an ACK/response.
	KindTimeout
	// KindReset: the peer answered with RST.
	KindReset
	// KindMalformed: the datagram could not be parsed.
	KindMalformed
	// KindExchangeExpired: a response arrived after ExchangeLifetime, when the
	// MID/Token could no longer be matched.
	KindExchangeExpired
	// KindUnmatched: an ACK/RST or response had no matching exchange.
	KindUnmatched
	// KindCanceled: the exchange was canceled via its context.
	KindCanceled
)

func (k FailureKind) String() string {
	switch k {
	case KindTimeout:
		return "timeout"
	case KindReset:
		return "reset"
	case KindMalformed:
		return "malformed"
	case KindExchangeExpired:
		return "exchange-expired"
	case KindUnmatched:
		return "unmatched"
	case KindCanceled:
		return "canceled"
	default:
		return "unknown"
	}
}

// Error carries a machine-readable FailureKind alongside the context.
type Error struct {
	Kind FailureKind
	Msg  string
}

func (e *Error) Error() string {
	return "protocol: " + e.Kind.String() + ": " + e.Msg
}

func fail(kind FailureKind, format string, args ...any) *Error {
	return &Error{Kind: kind, Msg: fmt.Sprintf(format, args...)}
}

// AsError extracts a *protocol.Error from an error.
func AsError(err error) (*Error, bool) {
	if e, ok := err.(*Error); ok {
		return e, true
	}
	return nil, false
}
