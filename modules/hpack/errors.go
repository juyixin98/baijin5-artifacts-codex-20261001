// Package hpack implements the HPACK header compression state machine
// (RFC 7541) on top of the byte-level codec in hpacklab.local/codec.
//
// The design is deliberately strict, because an HPACK decoder whose state
// desynchronizes from its peer silently corrupts every subsequent header
// block on a connection:
//
//   - Every malformed input is reported with a typed Kind so tests and logs
//     can distinguish an out-of-range index from bad Huffman padding.
//   - A failed DecodeBlock poisons the decoder; all later calls fail until
//     the (connection-scoped) decoder is replaced. This models the HTTP/2
//     requirement that a compression error is a connection error.
//   - Dynamic table sizing follows RFC 7541 sections 4.1/4.2 byte for byte:
//     every entry costs len(name)+len(value)+32 octets, and size updates are
//     legal only before the first header field representation of a block.
package hpack

import "fmt"

// Kind identifies the failure category of an HPACK error.
// It exists so callers never have to pattern-match message text.
type Kind int

const (
	// KindUnknown is the zero value and must never be produced.
	KindUnknown Kind = iota
	// KindIndexZero: an indexed representation referenced index 0, which is
	// never valid (RFC 7541 section 6.1).
	KindIndexZero
	// KindIndexOutOfRange: an index exceeded static+dynamic table extent.
	KindIndexOutOfRange
	// KindIntegerTruncated: a section-5.1 integer ended mid continuation.
	KindIntegerTruncated
	// KindIntegerOverflow: a section-5.1 integer exceeded 2^62-1.
	KindIntegerOverflow
	// KindStringTruncated: a string ran past the end of its block.
	KindStringTruncated
	// KindStringTooLong: a decompressed string exceeded the configured limit.
	KindStringTooLong
	// KindHuffmanInvalid: bad Huffman prefix, EOS in stream, or bad padding.
	KindHuffmanInvalid
	// KindSizeUpdatePosition: a size update appeared after a header field.
	KindSizeUpdatePosition
	// KindSizeUpdateTooLarge: a size update exceeded the SETTINGS-allowed cap.
	KindSizeUpdateTooLarge
	// KindHeaderListTooLarge: emitted headers exceeded the block size budget.
	KindHeaderListTooLarge
	// KindTruncatedBlock: a representation ran past the end of the block.
	KindTruncatedBlock
	// KindPoisoned: an earlier block already failed on this decoder.
	KindPoisoned
	// KindIllegalPseudo: pseudo-header after regular headers or duplicated.
	KindIllegalPseudo
)

// Error is the single error type returned by this package. Inspect Kind to
// classify failures; Offset, where known, is the byte offset inside the
// offending header block.
type Error struct {
	Kind   Kind
	Offset int
	Detail string
}

func (e *Error) Error() string {
	return fmt.Sprintf("hpack: %s at offset %d: %s", e.Kind, e.Offset, e.Detail)
}

// String returns a stable, machine-friendly category name (used in logs).
func (k Kind) String() string {
	switch k {
	case KindIndexZero:
		return "index-zero"
	case KindIndexOutOfRange:
		return "index-out-of-range"
	case KindIntegerTruncated:
		return "integer-truncated"
	case KindIntegerOverflow:
		return "integer-overflow"
	case KindStringTruncated:
		return "string-truncated"
	case KindStringTooLong:
		return "string-too-long"
	case KindHuffmanInvalid:
		return "huffman-invalid"
	case KindSizeUpdatePosition:
		return "size-update-illegal-position"
	case KindSizeUpdateTooLarge:
		return "size-update-too-large"
	case KindHeaderListTooLarge:
		return "header-list-too-large"
	case KindTruncatedBlock:
		return "block-truncated"
	case KindPoisoned:
		return "decoder-poisoned"
	case KindIllegalPseudo:
		return "illegal-pseudo-header"
	default:
		return "unknown"
	}
}
