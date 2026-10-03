// Package codec implements the byte-level primitives of HPACK (RFC 7541):
// prefixed integers (Section 5.1), string literals (Section 5.2) and the
// Huffman coding of Appendix B. It contains no protocol state; the state
// machine lives in package state.
package codec

import "errors"

// Byte-codec level error categories. Callers classify failures with
// errors.Is; every decode rejection maps to exactly one of these.
var (
	// ErrTruncated reports input that ends before the current item is
	// complete (integer continuation, string length, string payload).
	ErrTruncated = errors.New("codec: truncated input")

	// ErrIntegerOverflow reports a prefixed integer whose continuation
	// bytes would exceed 64 bits.
	ErrIntegerOverflow = errors.New("codec: integer overflow")

	// ErrHuffmanPadding reports a Huffman-coded string whose trailing
	// padding is longer than 7 bits or is not a prefix of the EOS symbol
	// (i.e. not all one-bits), as required by RFC 7541 Section 5.2.
	ErrHuffmanPadding = errors.New("codec: invalid huffman padding")

	// ErrHuffmanEOS reports a Huffman-coded string that contains the
	// EOS symbol (256) in the payload, which RFC 7541 Section 5.2
	// forbids.
	ErrHuffmanEOS = errors.New("codec: huffman EOS symbol in payload")

	// ErrStringTooLong reports a string literal whose declared or
	// decoded length exceeds the caller-configured limit.
	ErrStringTooLong = errors.New("codec: string literal exceeds limit")
)
