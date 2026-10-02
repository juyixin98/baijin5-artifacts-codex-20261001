// Package codec implements the HPACK primitive layer (RFC 7541 sections 5.1
// and 5.2): prefix integers, literal strings and the static Huffman code.
//
// This package is deliberately free of any table or header-block semantics:
// it is the byte-level codec used by the hpack state machine and covered by
// its own tests.
package codec

import "errors"

// Error categories returned by the primitive codec. Callers that need to
// assert a failure mode should use errors.Is against these sentinels rather
// than matching on message text.
var (
	// ErrIntegerOverflow means an integer representation kept supplying
	// continuation octets past the 63-bit accumulation bound.
	ErrIntegerOverflow = errors.New("hpack/codec: integer value overflow")
	// ErrTruncatedInteger means the representation ended while the integer
	// still had its continuation bit set.
	ErrTruncatedInteger = errors.New("hpack/codec: truncated integer")
	// ErrTruncatedString means the declared string runs past the buffer.
	ErrTruncatedString = errors.New("hpack/codec: truncated string")
	// ErrStringLengthLimit means a decoded string exceeded the configured
	// post-decompression length limit.
	ErrStringLengthLimit = errors.New("hpack/codec: string length limit exceeded")
	// ErrInvalidHuffman means the Huffman-encoded bytes are not a valid
	// encoding: an unknown prefix, an EOS (symbol 256) in the stream, more
	// than seven padding bits, or padding that is not an all-ones prefix of
	// EOS (RFC 7541 section 5.2).
	ErrInvalidHuffman = errors.New("hpack/codec: invalid huffman encoding")
	// ErrEmptyRepresentation means zero bytes were supplied where at least
	// one leading octet is required.
	ErrEmptyRepresentation = errors.New("hpack/codec: empty representation")
)

const maxIntegerBits = 63
