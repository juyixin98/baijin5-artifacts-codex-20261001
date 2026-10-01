package stun

// STUN protocol constants from RFC 5389 ("Session Traversal Utilities for
// NAT (STUN)"). Only the Binding method is implemented; TURN relay is
// explicitly out of scope.
const (
	// HeaderLen is the fixed STUN header size in bytes.
	HeaderLen = 20
	// MagicCookie is the RFC 5389 magic cookie (network byte order).
	MagicCookie uint32 = 0x2112A442
	// TransactionIDLen is the 96-bit transaction identifier length.
	TransactionIDLen = 12
	// attrHeaderLen is the per-attribute type+length prefix.
	attrHeaderLen = 4
	// paddingUnit is the STUN attribute alignment boundary.
	paddingUnit = 4
)

// MessageType is the 16-bit STUN message type (method + class encoded in
// non-contiguous bit positions per RFC 5389 §6).
type MessageType uint16

const (
	BindingRequest  MessageType = 0x0001
	BindingResponse MessageType = 0x0101
	BindingError    MessageType = 0x0111
)

// Method strips the class bits, leaving the 12-bit method.
func (t MessageType) Method() uint16 { return uint16(t) & 0x3EEF }

// Class returns the two-bit class (bits 8 and 4).
func (t MessageType) Class() uint16 { return uint16(t) & 0x0110 }

// IsKnown reports whether t is one of the three Binding message types this
// implementation understands.
func (t MessageType) IsKnown() bool {
	switch t {
	case BindingRequest, BindingResponse, BindingError:
		return true
	}
	return false
}

// AttrType identifies a STUN attribute. Attributes below 0x8000 are
// comprehension-required; unknown ones in that range force an error.
type AttrType uint16

const (
	AttrMappedAddress    AttrType = 0x0001
	AttrUsername         AttrType = 0x0006
	AttrMessageIntegrity AttrType = 0x0008
	AttrErrorCode        AttrType = 0x0009
	AttrUnknownAttrs     AttrType = 0x000A
	AttrRealm            AttrType = 0x0014
	AttrNonce            AttrType = 0x0015
	AttrXORMappedAddress AttrType = 0x0020
	// ICE (RFC 8445) attributes carried by Binding requests in practice.
	// They are recognized so such requests are not answered with 420; their
	// contents are not otherwise processed by this Binding-only server.
	AttrPriority       AttrType = 0x0024
	AttrUseCandidate   AttrType = 0x0025
	AttrIceControlled  AttrType = 0x8029
	AttrIceControlling AttrType = 0x802A
	AttrSoftware       AttrType = 0x8022
	AttrFingerprint    AttrType = 0x8028
)

// ComprehensionOptional marks the bit (0x8000) whose presence makes an
// attribute comprehension-optional per RFC 5389 §15.
const comprehensionOptionalBit AttrType = 0x8000

// Required reports whether the attribute is in the comprehension-required
// range.
func (a AttrType) Required() bool { return a&comprehensionOptionalBit == 0 }

// Address families used by MAPPED-ADDRESS / XOR-MAPPED-ADDRESS.
const (
	FamilyIPv4 byte = 0x01
	FamilyIPv6 byte = 0x02
)

// RFC 5389 error codes used in ERROR-CODE attributes.
const (
	StatusBadRequest       = 400
	StatusUnauthorized     = 401
	StatusUnknownAttribute = 420
	StatusServerError      = 500
)

// softwareVersion is the value of the SOFTWARE attribute in our responses.
const softwareVersion = "localstun-go/1.0 (RFC5389 binding, controlled-test)"
