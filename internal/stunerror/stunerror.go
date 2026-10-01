// Package stunerror defines the typed error categories shared across the STUN
// byte codec, client state machine and server. Every failure path returns an
// error whose Kind is one of the values below so tests can assert on the
// failure class instead of matching substrings.
package stunerror

import "errors"

// Kind partitions every detectable failure into a stable, testable category.
type Kind int

const (
	// KindUnknown is the zero value and must never be produced intentionally.
	KindUnknown Kind = iota
	// KindInput covers malformed bytes or arguments supplied by a caller:
	// truncation, impossible lengths, bad address families, etc.
	KindInput
	// KindState covers protocol state-machine conflicts: late responses,
	// duplicate transactions, responses that cannot complete any request.
	KindState
	// KindIntegrity covers MESSAGE-INTEGRITY failures and unknown
	// comprehension-required attributes.
	KindIntegrity
	// KindExhausted covers resource limits: transaction-table overflow,
	// too many outstanding requests, timeouts.
	KindExhausted
	// KindCompute covers cryptographic/OS computation failures.
	KindCompute
)

// Error is a categorized STUN error. Kind is the machine-readable failure
// class; Err carries the wrapped cause (which may itself be inspected with
// errors.Is/As).
type Error struct {
	Kind    Kind
	Op      string // short operation tag, e.g. "decode", "client.roundtrip"
	Message string
	Err     error
}

func (e *Error) Error() string {
	s := "stun"
	if e.Op != "" {
		s += "." + e.Op
	}
	s += ": " + e.Kind.String() + ": " + e.Message
	if e.Err != nil {
		s += ": " + e.Err.Error()
	}
	return s
}

func (e *Error) Unwrap() error { return e.Err }

// String returns a stable lowercase label used in logs and the audit DB.
func (k Kind) String() string {
	switch k {
	case KindInput:
		return "input"
	case KindState:
		return "state"
	case KindIntegrity:
		return "integrity"
	case KindExhausted:
		return "exhausted"
	case KindCompute:
		return "compute"
	default:
		return "unknown"
	}
}

// New builds an error of the given kind.
func New(kind Kind, op, msg string) *Error {
	return &Error{Kind: kind, Op: op, Message: msg}
}

// Wrap builds an error of the given kind carrying a cause.
func Wrap(kind Kind, op, msg string, cause error) *Error {
	return &Error{Kind: kind, Op: op, Message: msg, Err: cause}
}

// Kinded is implemented by typed protocol errors that carry a category
// without embedding *Error directly (e.g. stun.UnknownRequiredError).
type Kinded interface {
	Kind() Kind
}

// Of returns the Kind of a categorized STUN error, or KindUnknown when err is
// nil or carries no category.
func Of(err error) Kind {
	var se *Error
	if errors.As(err, &se) {
		return se.Kind
	}
	var k Kinded
	if errors.As(err, &k) {
		return k.Kind()
	}
	return KindUnknown
}
