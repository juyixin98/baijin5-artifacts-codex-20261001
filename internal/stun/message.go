package stun

import "net"

// TransactionID is the 96-bit STUN transaction identifier.
type TransactionID [TransactionIDLen]byte

// Attribute is a decoded STUN attribute: its type and raw (unpadded) value.
// The bytes in Value are owned by the Attribute; callers must not mutate a
// message returned by Decode without copying first.
type Attribute struct {
	Type  AttrType
	Value []byte
}

// Message is a decoded STUN message. IntegrityOK records whether an embedded
// MESSAGE-INTEGRITY attribute was present and validated against the supplied
// long-term key; it is false when no integrity attribute was carried.
type Message struct {
	Type        MessageType
	TxID        TransactionID
	Attrs       []Attribute
	IntegrityOK bool
}

// NewMessage builds an empty message of the given type with the given
// transaction id.
func NewMessage(t MessageType, txID TransactionID) *Message {
	return &Message{Type: t, TxID: txID}
}

// Add appends a raw attribute. The value is copied so later mutation of the
// caller's slice cannot affect the message.
func (m *Message) Add(t AttrType, value []byte) {
	v := make([]byte, len(value))
	copy(v, value)
	m.Attrs = append(m.Attrs, Attribute{Type: t, Value: v})
}

// Get returns the first attribute of the given type and whether it existed.
func (m *Message) Get(t AttrType) (Attribute, bool) {
	for _, a := range m.Attrs {
		if a.Type == t {
			return a, true
		}
	}
	return Attribute{}, false
}

// AddXORMappedAddress appends an XOR-MAPPED-ADDRESS attribute encoding ip
// (which must be a 4- or 16-byte net.IP) and port.
func (m *Message) AddXORMappedAddress(ip net.IP, port int) error {
	value, err := encodeXORAddress(ip, port, m.TxID)
	if err != nil {
		return err
	}
	m.Add(AttrXORMappedAddress, value)
	return nil
}

// XORMappedAddress decodes the XOR-MAPPED-ADDRESS attribute, if present and
// well-formed.
func (m *Message) XORMappedAddress() (net.IP, int, bool, error) {
	a, ok := m.Get(AttrXORMappedAddress)
	if !ok {
		return nil, 0, false, nil
	}
	ip, port, err := decodeXORAddress(a.Value, m.TxID)
	if err != nil {
		return nil, 0, true, err
	}
	return ip, port, true, nil
}

// AddSoftware appends the implementation SOFTWARE attribute.
func (m *Message) AddSoftware() {
	m.Add(AttrSoftware, []byte(softwareVersion))
}

// AddErrorCode appends an ERROR-CODE attribute (RFC 5389 §15.6):
// reserved(2 bytes) + class byte + number byte + UTF-8 reason.
func (m *Message) AddErrorCode(code int, reason string) {
	value := make([]byte, 4, 4+len(reason))
	value[2] = byte(code / 100) // hundreds digit
	value[3] = byte(code % 100) // remainder
	value = append(value, reason...)
	m.Add(AttrErrorCode, value)
}

// ErrorCode decodes an ERROR-CODE attribute if present.
func (m *Message) ErrorCode() (code int, reason string, ok bool) {
	a, found := m.Get(AttrErrorCode)
	if !found || len(a.Value) < 4 {
		// Present but malformed: callers cannot obtain a usable code.
		return 0, "", false
	}
	code = int(a.Value[2]&0x07)*100 + int(a.Value[3])
	reason = string(a.Value[4:])
	return code, reason, true
}
