package stun

import "errors"

// ErrorKind classifies a failure so callers (and evidence logs) can distinguish
// bad input from state conflicts, resource exhaustion and crypto failures.
type ErrorKind string

const (
	// KindInput: malformed bytes or invalid arguments at a system boundary.
	KindInput ErrorKind = "input_error"
	// KindUnknownCritical: a comprehension-required attribute (type < 0x8000)
	// the receiver does not understand; STUN 420 territory.
	KindUnknownCritical ErrorKind = "unknown_critical_attribute"
	// KindIntegrity: MESSAGE-INTEGRITY is missing/malformed or the HMAC does
	// not verify.
	KindIntegrity ErrorKind = "integrity_failure"
	// KindStateConflict: an operation is incompatible with current state,
	// e.g. completing a request that was already superseded or finalized.
	KindStateConflict ErrorKind = "state_conflict"
	// KindResourceExhausted: bounded capacity is full (pending transaction
	// table, read buffer budget).
	KindResourceExhausted ErrorKind = "resource_exhausted"
	// KindTimeout: no matching response arrived before the deadline.
	KindTimeout ErrorKind = "timeout"
	// KindSourceMismatch: a response's transport source address or transaction
	// id does not match the outstanding request.
	KindSourceMismatch ErrorKind = "source_mismatch"
	// KindCompute: encoding/crypto computation failed unexpectedly.
	KindCompute ErrorKind = "compute_failure"
)

// Error is the structured error used across module boundaries. Kind is the
// machine-readable failure category; Op names the operation that failed; Attr,
// when non-zero, identifies the attribute involved.
type Error struct {
	Kind   ErrorKind
	Op     string
	Attr   AttributeType
	Detail string
	Err    error
}

func (e *Error) Error() string {
	s := string(e.Kind)
	if e.Op != "" {
		s += " at " + e.Op
	}
	if e.Attr != 0 {
		s += " attr=0x" + hex4(uint16(e.Attr))
	}
	if e.Detail != "" {
		s += ": " + e.Detail
	}
	if e.Err != nil {
		s += ": " + e.Err.Error()
	}
	return s
}

func (e *Error) Unwrap() error { return e.Err }

// ErrorOf reports the ErrorKind of a stun.Error, or "" if err is not one.
func ErrorOf(err error) ErrorKind {
	var se *Error
	if errors.As(err, &se) {
		return se.Kind
	}
	return ""
}

func hex4(v uint16) string {
	const digits = "0123456789abcdef"
	b := make([]byte, 4)
	for i := range b {
		b[3-i] = digits[v&0xf]
		v >>= 4
	}
	return string(b)
}

// fail builds a *Error.
func fail(kind ErrorKind, op string, detail string) *Error {
	return &Error{Kind: kind, Op: op, Detail: detail}
}

func failAttr(kind ErrorKind, op string, attr AttributeType, detail string) *Error {
	return &Error{Kind: kind, Op: op, Attr: attr, Detail: detail}
}
