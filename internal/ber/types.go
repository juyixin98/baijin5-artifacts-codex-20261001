// Package ber implements a restricted, resource-bounded BER/DER codec.
//
// The codec is deliberately scoped to the types required by the service:
// INTEGER, BIT STRING, SEQUENCE/SET and context-specific tags.  Every
// resource dimension (nesting depth, tag number, length-of-length, content
// size, child count, integer width) is bounded by an explicit [Limits]
// value so that hostile input cannot exhaust memory or stack.
//
// All decode failures are reported as [DecodeError] values carrying a
// stable [ErrorKind] and the exact byte offset at which the violation was
// detected; a malformed value is never silently accepted.
package ber

import "fmt"

// Class is the ASN.1 tag class (X.690 8.1.2.1).
type Class uint8

const (
	ClassUniversal   Class = 0b00
	ClassApplication Class = 0b01
	ClassContext     Class = 0b10
	ClassPrivate     Class = 0b11
)

func (c Class) String() string {
	switch c {
	case ClassUniversal:
		return "universal"
	case ClassApplication:
		return "application"
	case ClassContext:
		return "context"
	case ClassPrivate:
		return "private"
	default:
		return "invalid"
	}
}

// Universal tag numbers supported by the restricted codec.
const (
	TagBoolean     uint32 = 0x01
	TagInteger     uint32 = 0x02
	TagBitString   uint32 = 0x03
	TagOctetString uint32 = 0x04
	TagNull        uint32 = 0x05
	TagSequence    uint32 = 0x10
	TagSet         uint32 = 0x11
)

// ErrorKind is a stable, machine-readable failure category.
type ErrorKind string

const (
	KindTruncated        ErrorKind = "TRUNCATED"          // input ended before a TLV was complete
	KindInvalidTag       ErrorKind = "INVALID_TAG"        // illegal/reserved identifier octets
	KindInvalidLength    ErrorKind = "INVALID_LENGTH"     // illegal length form (0xFF, primitive indefinite, ...)
	KindLengthOverflow   ErrorKind = "LENGTH_OVERFLOW"    // length-of-length exceeds the bound
	KindSizeExceeded     ErrorKind = "SIZE_EXCEEDED"      // a resource limit (content/tag/integer/children) was exceeded
	KindDepthExceeded    ErrorKind = "DEPTH_EXCEEDED"     // nesting depth exceeds the bound
	KindMalformedEOC     ErrorKind = "MALFORMED_EOC"      // end-of-contents octets in a place that is not a matching construction
	KindInvalidBitString ErrorKind = "INVALID_BIT_STRING" // unused-bits octet or trailing bits invalid
	KindInvalidInteger   ErrorKind = "INVALID_INTEGER"    // empty or illegal integer content
	KindInvalidEncoding  ErrorKind = "INVALID_ENCODING"   // well-formed TLV but illegal for the type
	KindTrailingData     ErrorKind = "TRAILING_DATA"      // bytes after the single top-level value
	KindUnsupported      ErrorKind = "UNSUPPORTED"        // encoding outside the restricted profile
	KindEncodeError      ErrorKind = "ENCODE_ERROR"       // encoder input is invalid
)

// DecodeError describes a decode failure with an exact byte offset.
//
// Offset semantics (contract used by the test suite):
//   - for truncation, Offset is the position at which another byte was
//     expected, i.e. len(input);
//   - for a bad octet, Offset is the index of that octet;
//   - for a bad length field, Offset is the index of its first length octet;
//   - for a misplaced EOC, Offset is the index of its leading 0x00 octet;
//   - for trailing data, Offset is the index of the first trailing byte.
type DecodeError struct {
	Kind   ErrorKind `json:"kind"`
	Offset int64     `json:"offset"`
	Depth  int       `json:"depth"`
	Msg    string    `json:"message"`
}

func (e *DecodeError) Error() string {
	return fmt.Sprintf("ber: %s at offset %d (depth %d): %s", e.Kind, e.Offset, e.Depth, e.Msg)
}

// Limits bounds every resource the decoder or encoder can consume.
type Limits struct {
	// MaxDepth is the maximum nesting depth of constructed values
	// (0 = top level only).
	MaxDepth int `json:"max_depth"`
	// MaxTagBytes is the maximum number of identifier octets, including
	// the initial identifier octet (long-tag form).
	MaxTagBytes int `json:"max_tag_bytes"`
	// MaxTagNumber bounds the decoded tag number (high-tag-number form).
	MaxTagNumber uint32 `json:"max_tag_number"`
	// MaxLengthBytes bounds the number of octets used to encode a length
	// (the "n" in the long-form 0x80|n).
	MaxLengthBytes int `json:"max_length_bytes"`
	// MaxContentBytes bounds the content length of any single TLV and the
	// total content span of an indefinite-length construction.
	MaxContentBytes int `json:"max_content_bytes"`
	// MaxChildren bounds the number of direct children of one value.
	MaxChildren int `json:"max_children"`
	// MaxIntegerBytes bounds the content width of an INTEGER in bytes.
	MaxIntegerBytes int `json:"max_integer_bytes"`
}

// DefaultLimits returns the conservative default resource profile.
func DefaultLimits() Limits {
	return Limits{
		MaxDepth:        32,
		MaxTagBytes:     4,
		MaxTagNumber:    0x1FFFFF, // 21 bits, matches MaxTagBytes 4
		MaxLengthBytes:  5,
		MaxContentBytes: 1 << 16, // 64 KiB
		MaxChildren:     4096,
		MaxIntegerBytes: 4096,
	}
}

// Normalize returns a copy with every unset/non-positive field replaced by
// the default profile's value.
func (l Limits) Normalize() Limits { return l.normalized() }

func (l Limits) normalized() Limits {
	d := DefaultLimits()
	if l.MaxDepth <= 0 {
		l.MaxDepth = d.MaxDepth
	}
	if l.MaxTagBytes <= 0 {
		l.MaxTagBytes = d.MaxTagBytes
	}
	if l.MaxTagNumber == 0 {
		l.MaxTagNumber = d.MaxTagNumber
	}
	if l.MaxLengthBytes <= 0 {
		l.MaxLengthBytes = d.MaxLengthBytes
	}
	if l.MaxContentBytes <= 0 {
		l.MaxContentBytes = d.MaxContentBytes
	}
	if l.MaxChildren <= 0 {
		l.MaxChildren = d.MaxChildren
	}
	if l.MaxIntegerBytes <= 0 {
		l.MaxIntegerBytes = d.MaxIntegerBytes
	}
	return l
}

// Node is one decoded (or to-be-encoded) ASN.1 value.
//
// Primitive values carry raw content in Value (for INTEGER that is the
// two's-complement content octets; for BIT STRING it includes the leading
// unused-bits octet).  Constructed values carry Children.
//
// Start/HeaderEnd/End record byte offsets into the source buffer (decode
// only; zero on encode) and are used for precise error reporting.
type Node struct {
	Class       Class   `json:"class"`
	Tag         uint32  `json:"tag"`
	Constructed bool    `json:"constructed"`
	Value       []byte  `json:"-"`
	Children    []*Node `json:"children,omitempty"`
	Indefinite  bool    `json:"indefinite_length,omitempty"`
	Start       int64   `json:"start_offset"`
	HeaderEnd   int64   `json:"header_end_offset,omitempty"`
	End         int64   `json:"end_offset"`

	// raw octets retained for DER canonical-form checks (not serialized)
	rawTag       []byte `json:"-"`
	rawLength    []byte `json:"-"`
	LengthOffset int64  `json:"-"`
}

// IsUniversal reports whether n carries the given universal tag.
func (n *Node) IsUniversal(tag uint32) bool { return n.Class == ClassUniversal && n.Tag == tag }

// Step is one parser progress event, used for correlated diagnostic logs.
type Step struct {
	Name   string // identifier | tag | length | enter | child | eoc | validate
	Offset int64
	Depth  int
	Detail string
}

// Tracer receives parser progress events.
type Tracer func(Step)
