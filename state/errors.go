// Package state implements the HPACK protocol state machine (RFC 7541
// Sections 3, 4 and 6): the decoder and encoder that drive a per-connection
// dynamic table, enforce representation legality (index validity, dynamic
// table size update placement) and decompression limits.
package state

import "errors"

// State-machine level error categories. Decode failures are classified
// with errors.Is against these sentinels; codec-level causes wrap
// codec.Err* values.
var (
	// ErrDesync is returned by every Decode call after a previous decode
	// failure on the same connection: once a header block fails, encoder
	// and decoder table state can no longer be assumed synchronized
	// (RFC 7540 Section 4.3 treats this as a connection error).
	ErrDesync = errors.New("state: decoder desynchronized after earlier failure")

	// ErrSizeUpdatePlacement reports a dynamic table size update that
	// appears after a header field representation in the same header
	// block. RFC 7541 Section 4.2 requires size updates at the beginning
	// of a header block.
	ErrSizeUpdatePlacement = errors.New("state: dynamic table size update after header field")

	// ErrSizeUpdateTooLarge reports a dynamic table size update whose
	// value exceeds the maximum the decoder announced via
	// SETTINGS_HEADER_TABLE_SIZE (RFC 7541 Section 6.3).
	ErrSizeUpdateTooLarge = errors.New("state: dynamic table size update exceeds configured maximum")

	// ErrHeaderListTooLarge reports a decompressed header list whose
	// total size (sum of name+value+32 per field) exceeds the configured
	// limit.
	ErrHeaderListTooLarge = errors.New("state: decompressed header list exceeds limit")

	// ErrTooManyHeaders reports a header list with more fields than the
	// configured limit.
	ErrTooManyHeaders = errors.New("state: header count exceeds limit")
)

// Limits bounds decompression work and output size. A zero value for a
// field disables that limit.
type Limits struct {
	// MaxHeaderListBytes bounds the sum of len(name)+len(value)+32 over
	// all decoded fields in one header block.
	MaxHeaderListBytes int

	// MaxHeaderCount bounds the number of decoded fields per block.
	MaxHeaderCount int

	// MaxStringLen bounds the declared and decoded length of each
	// individual string literal.
	MaxStringLen int
}

// DefaultLimits are conservative defaults suitable for a controlled
// service: 64 KiB of decompressed header list, 128 fields, 16 KiB per
// string literal.
var DefaultLimits = Limits{
	MaxHeaderListBytes: 64 << 10,
	MaxHeaderCount:     128,
	MaxStringLen:       16 << 10,
}
