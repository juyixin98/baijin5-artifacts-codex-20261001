package protocol

import "errors"

// Syntax errors returned by the path/parameter parsers. Messages are terse
// because they may appear in 501 replies; they carry no message content.
var (
	errMissingAddress   = errors.New("missing address")
	errUnterminatedPath = errors.New("unterminated <path>")
	errInvalidAddress   = errors.New("invalid mailbox syntax")
	errUnknownParameter = errors.New("unknown mail parameter")
)
