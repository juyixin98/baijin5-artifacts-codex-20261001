package ber

import "fmt"

// Category classifies every decode/encode failure so callers and tests can
// assert on the failure class, not just the message text.
type Category string

const (
	// CatTruncation: input ended before the current TLV was complete.
	// Offset is the position where more bytes were required (== len(input)
	// for a truncated tail).
	CatTruncation Category = "truncation"
	// CatSyntax: malformed identifier/length/content octets.
	CatSyntax Category = "syntax"
	// CatEOC: an end-of-contents octet pair was misused (appeared outside an
	// indefinite-length constructed value, or carried a non-zero length).
	CatEOC Category = "eoc"
	// CatResource: a configured resource limit was exceeded (tag length,
	// length-of-length, depth, node count, integer/bit-string size, input size).
	CatResource Category = "resource"
	// CatIndefinite: indefinite-length form used while disabled by Limits.
	CatIndefinite Category = "indefinite"
	// CatConstraint: DER/canonical constraint violated.
	CatConstraint Category = "constraint"
)

// Error is the single error type produced by this package. It always carries
// the byte offset (relative to the start of the input buffer) at which the
// failure was detected.
type Error struct {
	Category Category `json:"category"`
	Offset   int      `json:"offset"`
	Msg      string   `json:"message"`
}

func (e *Error) Error() string {
	return fmt.Sprintf("ber: %s at offset %d: %s", e.Category, e.Offset, e.Msg)
}

func errf(cat Category, off int, format string, args ...any) *Error {
	return &Error{Category: cat, Offset: off, Msg: fmt.Sprintf(format, args...)}
}
