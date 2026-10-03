// Package errs defines the error taxonomy shared across all module
// boundaries. Every failure crossing a module edge is classified into one
// of four categories so that the protocol layer can map it to a precise
// IMAP status and operators can distinguish input errors, state conflicts,
// resource exhaustion and computation failures in logs.
package errs

import "errors"

// Category classifies a failure crossing a module boundary.
type Category int

const (
	// CatInput is malformed or unsupported client input (parse errors,
	// undeclared data items). Maps to tagged BAD.
	CatInput Category = iota + 1
	// CatState is well-formed input that conflicts with protocol or
	// mailbox state (no mailbox selected, unknown mailbox, stale UID).
	// Maps to tagged NO.
	CatState
	// CatResource is a exceeded limit (line, literal, sequence-set
	// expansion). Maps to untagged BYE at read time, tagged NO at
	// execution time.
	CatResource
	// CatInternal is a store or computation failure. Maps to tagged NO
	// with an opaque message; details are logged, never leaked.
	CatInternal
)

func (c Category) String() string {
	switch c {
	case CatInput:
		return "input"
	case CatState:
		return "state"
	case CatResource:
		return "resource"
	case CatInternal:
		return "internal"
	}
	return "unknown"
}

// Error is the typed error contract between modules.
type Error struct {
	Cat Category
	Op  string // logical operation, e.g. "wire.read", "store.expunge"
	Msg string // safe to show to the client
	Err error  // wrapped cause, never sent to the client
}

func (e *Error) Error() string {
	if e.Err != nil {
		return e.Op + ": " + e.Msg + ": " + e.Err.Error()
	}
	return e.Op + ": " + e.Msg
}

func (e *Error) Unwrap() error { return e.Err }

// New classifies a failure without a wrapped cause.
func New(cat Category, op, msg string) *Error {
	return &Error{Cat: cat, Op: op, Msg: msg}
}

// Wrap classifies a failure with a wrapped cause.
func Wrap(cat Category, op string, err error, msg string) *Error {
	return &Error{Cat: cat, Op: op, Msg: msg, Err: err}
}

// CategoryOf extracts the category of err, defaulting to CatInternal for
// unclassified errors (fail-safe: internals are never leaked to clients).
func CategoryOf(err error) Category {
	var e *Error
	if errors.As(err, &e) {
		return e.Cat
	}
	return CatInternal
}
