package ber

import (
	"encoding/binary"
	"fmt"
)

// Encoding selects the wire form produced by [Encode].
type Encoding int

const (
	// BER produces definite-length encoding, valid BER and DER content.
	BER Encoding = iota
	// BERIndefinite produces indefinite-length encoding for every
	// constructed value (primitives must use definite length).
	BERIndefinite
	// DER produces canonical definite-length encoding.
	DER
)

// encLimits bounds encoder output so an invalid tree cannot trigger
// unbounded allocation. Derived from Limits but static for the encoder.
const (
	encMaxTagBytes    = 5
	encMaxLengthBytes = 5
	encMaxContent     = 1 << 20
)

// Encode serialises n in the requested form. It returns an ENCODE_ERROR
// category failure for invalid input (unsupported class/tag, indefinite
// request on a primitive, oversized content, unbalanced tree).
func Encode(n *Node, enc Encoding) []byte {
	out, err := encodeChecked(n, enc, 1)
	if err != nil {
		panic(err) // use EncodeChecked for error-returning encoding
	}
	return out
}

// EncodeChecked is like Encode but returns the DecodeError (Kind
// KindEncodeError) instead of panicking.
func EncodeChecked(n *Node, enc Encoding) ([]byte, error) {
	out, err := encodeChecked(n, enc, 1)
	if err != nil {
		return nil, err
	}
	return out, nil
}

func encodeError(n *Node, format string, args ...any) *DecodeError {
	off := int64(-1)
	depth := 0
	if n != nil {
		off, depth = n.Start, 0
	}
	return &DecodeError{Kind: KindEncodeError, Offset: off, Depth: depth, Msg: fmt.Sprintf(format, args...)}
}

func encodeChecked(n *Node, enc Encoding, depth int) ([]byte, *DecodeError) {
	if n == nil {
		return nil, encodeError(nil, "nil node")
	}
	if depth > DefaultLimits().MaxDepth {
		return nil, encodeError(n, "encoding depth exceeds bound")
	}
	switch n.Class {
	case ClassUniversal, ClassContext:
	default:
		return nil, encodeError(n, "class %s is outside the restricted profile", n.Class)
	}
	id, err := encodeIdentifier(n, enc)
	if err != nil {
		return nil, err
	}

	if n.Constructed {
		return encodeConstructed(n, enc, id, depth)
	}
	// Primitives always use definite length, even under BERIndefinite:
	// X.690 8.1.3.2 forbids indefinite length on primitive values.
	if n.Value == nil {
		// distinguish absent content from empty content
		return nil, encodeError(n, "primitive node has no content")
	}
	return encodeTLV(id, n.Value, nil), nil
}

func encodeConstructed(n *Node, enc Encoding, id []byte, depth int) ([]byte, *DecodeError) {
	// DER requires ascending encoded order for SET/SET OF (X.690 11.6).
	// Encode children first (a copy of order is used; the node is not
	// mutated), then sort the encoded elements for a universal SET.
	parts := make([][]byte, 0, len(n.Children))
	for _, c := range n.Children {
		b, err := encodeChecked(c, enc, depth+1)
		if err != nil {
			return nil, err
		}
		parts = append(parts, b)
	}
	if enc == DER && n.IsUniversal(TagSet) {
		sortByteSlices(parts)
	}
	var content []byte
	for _, b := range parts {
		if len(content)+len(b) > encMaxContent {
			return nil, encodeError(n, "constructed content exceeds %d bytes", encMaxContent)
		}
		content = append(content, b...)
	}
	if enc == BERIndefinite {
		out := make([]byte, 0, len(id)+1+len(content)+2)
		out = append(out, id...)
		out = append(out, 0x80)
		out = append(out, content...)
		out = append(out, 0x00, 0x00)
		return out, nil
	}
	return encodeTLV(id, content, nil), nil
}

func sortByteSlices(a [][]byte) {
	for i := 1; i < len(a); i++ {
		for j := i; j > 0 && bytesCompare(a[j-1], a[j]) > 0; j-- {
			a[j-1], a[j] = a[j], a[j-1]
		}
	}
}

func bytesCompare(a, b []byte) int {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return int(a[i]) - int(b[i])
		}
	}
	return len(a) - len(b)
}

func encodeTLV(id, content, _ []byte) []byte {
	out := make([]byte, 0, len(id)+encMaxLengthBytes+len(content))
	out = append(out, id...)
	out = appendLength(out, len(content))
	out = append(out, content...)
	return out
}

func appendLength(out []byte, v int) []byte {
	switch {
	case v < 0x80:
		return append(out, byte(v))
	case v <= 0xFF:
		return append(out, 0x81, byte(v))
	case v <= 0xFFFF:
		return append(out, 0x82, byte(v>>8), byte(v))
	case v <= 0xFFFFFF:
		return append(out, 0x83, byte(v>>16), byte(v>>8), byte(v))
	default:
		var buf [4]byte
		binary.BigEndian.PutUint32(buf[:], uint32(v))
		return append(out, 0x84, buf[0], buf[1], buf[2], buf[3])
	}
}

func encodeIdentifier(n *Node, enc Encoding) ([]byte, *DecodeError) {
	first := byte(n.Class) << 6
	if n.Constructed {
		first |= 0x20
	}
	if n.Tag < 0x1F {
		return []byte{first | byte(n.Tag)}, nil
	}
	if n.Tag > DefaultLimits().MaxTagNumber {
		return nil, encodeError(n, "tag number %d exceeds bound", n.Tag)
	}
	// Minimal base-128 digits, big-endian.
	var digits []byte
	v := n.Tag
	for {
		digits = append(digits, byte(v&0x7F))
		v >>= 7
		if v == 0 {
			break
		}
	}
	if len(digits)+1 > encMaxTagBytes {
		return nil, encodeError(n, "tag requires more than %d octets", encMaxTagBytes)
	}
	out := []byte{first | 0x1F}
	for i := len(digits) - 1; i >= 0; i-- {
		oct := digits[i]
		if i > 0 {
			oct |= 0x80
		}
		out = append(out, oct)
	}
	return out, nil
}

// ---- typed constructors (the ergonomic API used by the service) ----

// IntegerNode builds a primitive INTEGER node from a big integer carried
// as sign-magnitude; use [IntegerFromInt64] for machine integers.
func IntegerNode(v int64) *Node {
	return &Node{Class: ClassUniversal, Tag: TagInteger, Value: encodeInt64(v)}
}

func encodeInt64(v int64) []byte {
	if v == 0 {
		return []byte{0}
	}
	u := uint64(v) // int64->uint64 preserves the two's-complement bit pattern
	buf := make([]byte, 8)
	binary.BigEndian.PutUint64(buf, u)
	i := 0
	if v > 0 {
		// drop leading 0x00 while the next octet's sign bit stays clear
		for i < 7 && buf[i] == 0x00 && buf[i+1]&0x80 == 0 {
			i++
		}
	} else {
		// drop leading 0xFF while the next octet's sign bit stays set
		for i < 7 && buf[i] == 0xFF && buf[i+1]&0x80 != 0 {
			i++
		}
	}
	return buf[i:]
}

// BitStringNode builds a primitive BIT STRING node. unusedBits is 0..7 and
// the trailing unused bits must already be zero; data is the payload.
func BitStringNode(data []byte, unusedBits uint8) (*Node, error) {
	content := make([]byte, 0, 1+len(data))
	content = append(content, unusedBits)
	content = append(content, data...)
	n := &Node{Class: ClassUniversal, Tag: TagBitString, Value: content}
	if e := validateRec(n, DefaultLimits().normalized(), false, 0); e != nil {
		return nil, e
	}
	return n, nil
}

// SequenceNode builds a SEQUENCE of children.
func SequenceNode(children ...*Node) *Node {
	return &Node{Class: ClassUniversal, Tag: TagSequence, Constructed: true, Children: children}
}

// SetNode builds a SET/SET OF of children.
func SetNode(children ...*Node) *Node {
	return &Node{Class: ClassUniversal, Tag: TagSet, Constructed: true, Children: children}
}

// ContextNode builds a context-specific tagged value.
func ContextNode(tag uint32, constructed bool, children []*Node, value []byte) *Node {
	return &Node{Class: ClassContext, Tag: tag, Constructed: constructed, Children: children, Value: value}
}

// NullNode returns the canonical NULL.
func NullNode() *Node {
	return &Node{Class: ClassUniversal, Tag: TagNull, Value: []byte{}}
}

// BoolNode builds a BOOLEAN.
func BoolNode(v bool) *Node {
	if v {
		return &Node{Class: ClassUniversal, Tag: TagBoolean, Value: []byte{0xFF}}
	}
	return &Node{Class: ClassUniversal, Tag: TagBoolean, Value: []byte{0x00}}
}

// IntegerValue decodes an INTEGER node's content as an int64 plus an
// "overflow" flag for values wider than 64 bits (raw content returned in
// that case via RawInteger).
func IntegerValue(n *Node) (int64, bool, error) {
	if !n.IsUniversal(TagInteger) {
		return 0, false, fmt.Errorf("not an INTEGER")
	}
	if len(n.Value) == 0 {
		return 0, false, fmt.Errorf("empty INTEGER")
	}
	if len(n.Value) <= 8 {
		var u uint64
		for _, b := range n.Value {
			u = u<<8 | uint64(b)
		}
		// sign-extend from the actual content width via an arithmetic shift
		shift := uint(64 - len(n.Value)*8)
		v := int64(u)
		v = v << shift >> shift
		return v, false, nil
	}
	return 0, true, nil
}

// RawInteger returns the minimal two's-complement content bytes.
func RawInteger(n *Node) ([]byte, error) {
	if !n.IsUniversal(TagInteger) {
		return nil, fmt.Errorf("not an INTEGER")
	}
	return append([]byte(nil), n.Value...), nil
}

// BitStringValue splits a primitive BIT STRING into payload and unused count.
func BitStringValue(n *Node) (data []byte, unusedBits uint8, err error) {
	if !n.IsUniversal(TagBitString) || n.Constructed {
		return nil, 0, fmt.Errorf("not a primitive BIT STRING")
	}
	if len(n.Value) == 0 {
		return nil, 0, fmt.Errorf("empty BIT STRING")
	}
	return n.Value[1:], n.Value[0], nil
}
