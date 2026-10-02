// Package imapwire implements the byte-level IMAP4rev1 codec used by the
// server: a streaming command reader (atoms, quoted strings, literals) and a
// response writer that chooses between atom, quoted-string and literal
// encodings. It deliberately knows nothing about command semantics.
package imapwire

import "fmt"

// Class is the machine-readable failure category attached to every error.
// The set is intentionally closed so callers and tests can distinguish the
// four buckets required by the service contract: bad input, state conflicts,
// resource exhaustion and computation failures, plus finer auth sub-classes.
type Class string

const (
	ClassInput           Class = "input-error"
	ClassUnknownCommand  Class = "unknown-command"
	ClassUnsupportedItem Class = "unsupported-data-item"
	ClassState           Class = "state-conflict"
	ClassAuth            Class = "auth-failure"
	ClassAuthUnsupported Class = "auth-unsupported"
	ClassPermission      Class = "permission-denied"
	ClassResource        Class = "resource-exhausted"
	ClassCompute         Class = "computation-failure"
)

// Error is the explicit cross-layer error contract. Every protocol failure
// surfaces as an *Error so the session layer can map it to an IMAP status
// (BAD/NO) and a response code without string matching.
type Error struct {
	Class Class
	// Code is an optional RFC 5530/3501 response-code token, e.g. PARSE,
	// TOOBIG, UIDVALIDITY, AUTHENTICATIONFAILED, SERVERBUG.
	Code string
	Msg  string
}

func (e *Error) Error() string { return string(e.Class) + ": " + e.Msg }

// NewError builds an *Error.
func NewError(c Class, format string, args ...any) *Error {
	return &Error{Class: c, Msg: fmt.Sprintf(format, args...)}
}

// NewCodedError builds an *Error carrying an IMAP response code.
func NewCodedError(c Class, code, format string, args ...any) *Error {
	return &Error{Class: c, Code: code, Msg: fmt.Sprintf(format, args...)}
}
