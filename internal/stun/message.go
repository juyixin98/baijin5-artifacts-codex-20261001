package stun

import (
	"crypto/rand"
	"encoding/binary"
	"fmt"
)

// TransactionID is the 96-bit STUN transaction identifier.
type TransactionID [12]byte

// Message is a parsed STUN message. Attributes preserve wire order; Raw is the
// exact datagram bytes when the message came from UnmarshalMessage (needed for
// MESSAGE-INTEGRITY verification) and nil for locally built messages.
type Message struct {
	Method        Method
	Class         Class
	TransactionID TransactionID
	Attributes    []Attribute
	Raw           []byte
}

// NewTransactionID draws a transaction id from a CSPRNG.
func NewTransactionID() (TransactionID, error) {
	var id TransactionID
	if _, err := rand.Read(id[:]); err != nil {
		return TransactionID{}, &Error{Kind: KindCompute, Op: "NewTransactionID",
			Detail: "cannot read random bytes", Err: err}
	}
	return id, nil
}

// Attribute returns the value and presence flag of the first attribute of the
// given type.
func (m *Message) Attribute(t AttributeType) ([]byte, bool) {
	for _, a := range m.Attributes {
		if a.Type == t {
			return a.Value, true
		}
	}
	return nil, false
}

// marshal serialises header + attributes. attrBody is the already-encoded
// attribute block; message length covers exactly that block.
func marshal(method Method, class Class, txnID TransactionID, attrBody []byte) []byte {
	buf := make([]byte, HeaderSize+len(attrBody))
	binary.BigEndian.PutUint16(buf[0:2], EncodeType(method, class))
	binary.BigEndian.PutUint16(buf[2:4], uint16(len(attrBody)))
	binary.BigEndian.PutUint32(buf[4:8], MagicCookie)
	copy(buf[8:20], txnID[:])
	copy(buf[HeaderSize:], attrBody)
	return buf
}

// Marshal builds a STUN message without MESSAGE-INTEGRITY.
func Marshal(method Method, class Class, txnID TransactionID, attrs []Attribute) ([]byte, error) {
	body, err := EncodeAttributes(attrs)
	if err != nil {
		return nil, err
	}
	return marshal(method, class, txnID, body), nil
}

// UnmarshalMessage parses and structurally validates one datagram:
//
//   - at least 20 bytes, leading two bits zero, magic cookie present
//   - header length equals the number of trailing bytes exactly
//   - the attribute block parses as well-formed TLVs with correct padding
//
// It deliberately does NOT enforce comprehension rules or integrity; those are
// separate policy steps so callers can attach the right error category.
func UnmarshalMessage(b []byte) (*Message, error) {
	const op = "UnmarshalMessage"
	if len(b) < HeaderSize {
		return nil, fail(KindInput, op,
			fmt.Sprintf("datagram shorter than 20-byte header: %d bytes", len(b)))
	}
	if b[0]&0xC0 != 0 {
		return nil, fail(KindInput, op,
			fmt.Sprintf("leading two bits must be zero, got 0x%02x", b[0]))
	}
	if got := binary.BigEndian.Uint32(b[4:8]); got != MagicCookie {
		return nil, fail(KindInput, op,
			fmt.Sprintf("bad magic cookie: 0x%08x", got))
	}
	declared := int(binary.BigEndian.Uint16(b[2:4]))
	if declared != len(b)-HeaderSize {
		return nil, fail(KindInput, op,
			fmt.Sprintf("message length %d does not match datagram body %d",
				declared, len(b)-HeaderSize))
	}
	method, class := DecodeType(binary.BigEndian.Uint16(b[0:2]))
	m := &Message{
		Method: method,
		Class:  class,
		Raw:    append([]byte(nil), b...),
	}
	copy(m.TransactionID[:], b[8:20])
	if declared > 0 {
		attrs, err := DecodeAttributes(b[HeaderSize:])
		if err != nil {
			return nil, err
		}
		m.Attributes = attrs
	}
	return m, nil
}

// UnknownRequired returns comprehension-required attribute types in attrs that
// the understood predicate does not recognise, preserving first-seen order.
func UnknownRequired(attrs []Attribute, understood func(AttributeType) bool) []AttributeType {
	seen := map[AttributeType]bool{}
	var unknown []AttributeType
	for _, a := range attrs {
		if a.Type.IsComprehensionRequired() && !understood(a.Type) && !seen[a.Type] {
			seen[a.Type] = true
			unknown = append(unknown, a.Type)
		}
	}
	return unknown
}
