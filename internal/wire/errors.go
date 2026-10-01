package wire

import "errors"

// Frame-level errors. These describe malformed datagrams only; protocol
// policy failures live in the protocol/blockwise packages.
var (
	// ErrMalformed is the umbrella error for a datagram that cannot be parsed.
	ErrMalformed = errors.New("wire: malformed message")
	// ErrVersion is returned when the version nibble is not 1.
	ErrVersion = errors.New("wire: unsupported version")
	// ErrBadTokenLength is returned for TKL values that run past the datagram.
	ErrBadTokenLength = errors.New("wire: token length exceeds datagram")
	// ErrOptionOrder is returned when options are not strictly ascending.
	ErrOptionOrder = errors.New("wire: options out of order")
	// ErrPayloadMarker is returned for a stray payload marker / truncated data.
	ErrPayloadMarker = errors.New("wire: invalid payload marker")
	// ErrTooLarge is returned when an encoded datagram exceeds MaxDatagram.
	ErrTooLarge = errors.New("wire: message too large for one datagram")
	// ErrEmptyPayload is returned when the marker is present but no bytes follow.
	ErrEmptyPayload = errors.New("wire: payload marker with empty payload")
	// ErrBlockOption is returned for a malformed Block1/Block2 option.
	ErrBlockOption = errors.New("wire: malformed block option")
)
