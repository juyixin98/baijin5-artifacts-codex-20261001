// Package stun implements the subset of RFC 5389 / RFC 8489 needed for a
// controlled, local STUN Binding test lab:
//
//   - Binding request / success response / error response messages
//   - MAPPED-ADDRESS and XOR-MAPPED-ADDRESS (IPv4 and IPv6)
//   - MESSAGE-INTEGRITY (HMAC-SHA1, short-term shared secret, test only)
//   - ERROR-CODE and UNKNOWN-ATTRIBUTES (420) handling
//
// It explicitly does NOT implement TURN relaying, long-term authentication,
// FINGERPRINT emission, or any NAT discovery procedures.
package stun

// HeaderSize is the fixed STUN header length in bytes.
const HeaderSize = 20

// MagicCookie is the fixed RFC 5389 magic cookie (network byte order on the
// wire as 21 12 A4 42).
const MagicCookie uint32 = 0x2112A442

// Method is a STUN method. Values use the on-wire encoding (method bits only,
// class bits masked out), e.g. Binding is 0x0001.
type Method uint16

const (
	MethodBinding Method = 0x0001
)

// Class is a STUN message class. Values use the on-wire encoding so that
// message type == method | class.
type Class uint16

const (
	ClassRequest         Class = 0x0000
	ClassIndication      Class = 0x0010
	ClassSuccessResponse Class = 0x0100
	ClassErrorResponse   Class = 0x0110
)

// classMask isolates the two class bits (positions 4 and 8) of a message type.
const classMask uint16 = 0x0110

// methodMask clears the two class bits, leaving the 12 method bits.
const methodMask uint16 = ^classMask

// EncodeType combines a method and a class into the on-wire message type.
func EncodeType(method Method, class Class) uint16 {
	return uint16(method) | uint16(class)
}

// DecodeType splits an on-wire message type into method and class.
func DecodeType(t uint16) (Method, Class) {
	return Method(t & methodMask), Class(t & classMask)
}

// AttributeType identifies a STUN attribute.
type AttributeType uint16

const (
	AttrMappedAddress     AttributeType = 0x0001
	AttrUsername          AttributeType = 0x0006
	AttrMessageIntegrity  AttributeType = 0x0008
	AttrErrorCode         AttributeType = 0x0009
	AttrXORMappedAddress  AttributeType = 0x0020
	AttrUnknownAttributes AttributeType = 0x8005 // comprehension-optional
	AttrSoftware          AttributeType = 0x8022 // comprehension-optional
	AttrFingerprint       AttributeType = 0x8028 // comprehension-optional, parse only
)

// IsComprehensionRequired reports whether receivers must understand the
// attribute: attribute types below 0x8000 are mandatory per RFC 5389 15.
func (t AttributeType) IsComprehensionRequired() bool {
	return uint16(t)&0x8000 == 0
}
