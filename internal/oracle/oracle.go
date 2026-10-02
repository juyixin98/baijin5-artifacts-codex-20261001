// Package oracle wraps the mature third-party library
// github.com/go-asn1-ber as an *independent* reference decoder. The
// compatibility suite decodes values with this oracle and compares them
// against the restricted codec under test; the oracle is never
// implemented by the code under test.
//
// Known, deliberately-accepted divergences are exposed explicitly via
// [DecodeResult] fields rather than hidden, so the compatibility test
// can assert the reason for each difference.
package oracle

import (
	"bytes"

	berlib "github.com/go-asn1-ber/asn1-ber"
)

// Class mirrors a tag class for cross-package comparison.
type Class uint8

const (
	ClassUniversal Class = 0
	ClassContext   Class = 2
)

// ORNode is the oracle-neutral value representation.
type ORNode struct {
	Class       Class
	Tag         uint32
	Constructed bool
	Value       []byte
	Children    []*ORNode
}

// DecodeResult is the independent library's verdict for one input.
type DecodeResult struct {
	OK bool
	// Node is the first decoded packet (nil on error).
	Node *ORNode
	// ParseError is non-nil when the mature library rejects the input.
	ParseError error
	// Consumed is how many bytes the library read for the first packet.
	Consumed int
	// Rest is len(input)-Consumed. The library decodes only the first
	// TLV; bytes after it are not an error to it.
	Rest int
	// IsEOC is true when the first packet is a bare end-of-contents
	// (00 00) that the library accepts at its read position but which
	// terminates no indefinite construction.
	IsEOC bool
}

// Decode asks the independent library to decode the first BER value.
// Byte consumption is measured through the reader position, since the
// library's DecodePacketErr does not report it.
func Decode(data []byte) DecodeResult {
	r := bytes.NewReader(data)
	before := r.Len()
	packet, err := berlib.ReadPacket(r)
	if err != nil {
		return DecodeResult{OK: false, ParseError: err, Consumed: 0, Rest: len(data)}
	}
	consumed := int(before - r.Len())
	res := DecodeResult{
		OK:       true,
		Node:     convert(packet),
		Consumed: consumed,
		Rest:     len(data) - consumed,
	}
	if packet.ClassType == berlib.ClassUniversal && packet.Tag == berlib.TagEOC &&
		packet.TagType == berlib.TypePrimitive {
		res.IsEOC = true
	}
	return res
}

func convert(p *berlib.Packet) *ORNode {
	n := &ORNode{
		Tag:         uint32(p.Tag),
		Constructed: p.TagType == berlib.TypeConstructed,
	}
	switch p.ClassType {
	case berlib.ClassUniversal:
		n.Class = ClassUniversal
	case berlib.ClassContext:
		n.Class = ClassContext
	default:
		n.Class = Class(p.ClassType >> 6)
	}
	if p.TagType == berlib.TypeConstructed {
		for _, c := range p.Children {
			n.Children = append(n.Children, convert(c))
		}
	} else if p.ByteValue != nil {
		n.Value = append([]byte(nil), p.ByteValue...)
	}
	return n
}

// IntegerBytes asks the oracle to encode an INTEGER from int64 and
// returns the complete TLV.
func IntegerBytes(v int64) []byte {
	p := berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, v, "")
	return p.Bytes()
}

// IntegerBytesRaw builds an INTEGER TLV around raw two's-complement
// content using the library's packet machinery (the library's typed
// constructor only handles machine-width integers, so content is
// supplied directly for "超长整数" cases).
func IntegerBytesRaw(twos []byte) []byte {
	p := berlib.Encode(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, nil, "")
	p.Data.Write(twos)
	return p.Bytes()
}

// SequenceBytes asks the oracle to encode a SEQUENCE of child TLVs.
func SequenceBytes(children ...[]byte) []byte {
	p := berlib.Encode(berlib.ClassUniversal, berlib.TypeConstructed, berlib.TagSequence, nil, "")
	appendEncodedChildren(p, children)
	return p.Bytes()
}

// ContextBytes asks the oracle to encode a constructed context tag.
func ContextBytes(tag uint32, children ...[]byte) []byte {
	p := berlib.Encode(berlib.ClassContext, berlib.TypeConstructed, berlib.Tag(tag), nil, "")
	appendEncodedChildren(p, children)
	return p.Bytes()
}

// IndefiniteWrap produces indefinite-length framing around child TLVs
// that were encoded by the mature library:
//
//	identifier 0x80 <children> 0x00 0x00
//
// go-asn1-ber v1.5.8 always emits definite length (Packet.Bytes has no
// indefinite option), so the X.690 8.1.3.6 framing octets are applied
// here while every child encoding stays library-produced.
func IndefiniteWrap(identifier byte, children ...[]byte) []byte {
	out := []byte{identifier, 0x80}
	for _, c := range children {
		out = append(out, c...)
	}
	out = append(out, 0x00, 0x00)
	return out
}

func appendEncodedChildren(p *berlib.Packet, children [][]byte) {
	for _, cb := range children {
		cp, err := berlib.DecodePacketErr(cb)
		if err != nil {
			continue
		}
		p.AppendChild(cp)
	}
}
