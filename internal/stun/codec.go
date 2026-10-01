package stun

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"net"

	"localstun/internal/stunerror"
)

// fingerprintXOR is the RFC 5389 FINGERPRINT CRC-32 XOR mask.
const fingerprintXOR uint32 = 0x5354554E

// fingerprintLen is FINGERPRINT value length (a 32-bit CRC).
const fingerprintLen = 4

// integrityLen is MESSAGE-INTEGRITY value length (HMAC-SHA1).
const integrityLen = 20

// DecodeError sentinel values wrapped inside *stunerror.Error so callers can
// distinguish concrete failure causes with errors.Is.
var (
	ErrShortMessage        = errors.New("message shorter than 20-byte header")
	ErrLeadingBits         = errors.New("first two bits must be zero")
	ErrBadCookie           = errors.New("magic cookie mismatch")
	ErrBadLength           = errors.New("message length field does not match body size")
	ErrTruncatedAttr       = errors.New("attribute runs past end of message")
	ErrUnknownMethod       = errors.New("unsupported STUN method/class")
	ErrAttrAfterIntegrity  = errors.New("only FINGERPRINT may follow MESSAGE-INTEGRITY")
	ErrIntegrityMismatch   = errors.New("MESSAGE-INTEGRITY HMAC mismatch")
	ErrFingerprintMismatch = errors.New("FINGERPRINT CRC mismatch")
)

// Marshal serializes m. Attributes are laid out in their slice order, each
// padded to a multiple of four bytes. If key is non-nil a MESSAGE-INTEGRITY
// attribute is appended, computed per RFC 5389 §15.4 over HMAC-SHA1. If
// fingerprint is true a FINGERPRINT attribute follows (requires key != nil).
func Marshal(m *Message, key []byte, fingerprint bool) ([]byte, error) {
	if !m.Type.IsKnown() {
		return nil, stunerror.New(stunerror.KindInput, "encode", "unknown message type 0x"+hex16(uint16(m.Type)))
	}
	if fingerprint && key == nil {
		return nil, stunerror.New(stunerror.KindInput, "encode", "FINGERPRINT requires MESSAGE-INTEGRITY")
	}

	body := make([]byte, 0, 128)
	for _, a := range m.Attrs {
		if a.Type == AttrMessageIntegrity || a.Type == AttrFingerprint {
			return nil, stunerror.New(stunerror.KindInput, "encode", "integrity/fingerprint must not be preset")
		}
		body = marshalAttr(body, a.Type, a.Value)
	}

	if key == nil {
		return frame(m, body), nil
	}

	// HMAC input (RFC 5389 §15.4): header + body up to (not including) the
	// MESSAGE-INTEGRITY attribute, with the header Length temporarily set to
	// include the 24-byte integrity attribute.
	hmacInput := frameWithLength(m, body, HeaderLen+len(body)+attrHeaderLen+integrityLen)
	mac := hmacSHA1(key, hmacInput)
	body = marshalAttr(body, AttrMessageIntegrity, mac)

	if fingerprint {
		// CRC input: whole message up to (not including) FINGERPRINT, with
		// Length advanced by the 8-byte fingerprint attribute.
		crcInput := frameWithLength(m, body, HeaderLen+len(body)+attrHeaderLen+fingerprintLen)
		crc := crc32.ChecksumIEEE(crcInput) ^ fingerprintXOR
		var crcBytes [4]byte
		binary.BigEndian.PutUint32(crcBytes[:], crc)
		body = marshalAttr(body, AttrFingerprint, crcBytes[:])
	}

	return frame(m, body), nil
}

func marshalAttr(dst []byte, t AttrType, value []byte) []byte {
	var hdr [4]byte
	binary.BigEndian.PutUint16(hdr[0:2], uint16(t))
	binary.BigEndian.PutUint16(hdr[2:4], uint16(len(value)))
	dst = append(dst, hdr[:]...)
	dst = append(dst, value...)
	for i := 0; i < paddingFor(len(value)); i++ {
		dst = append(dst, 0)
	}
	return dst
}

// paddingFor returns the number of zero padding bytes after a value of length
// n so that every attribute occupies a multiple of four bytes (RFC 5389 §15).
func paddingFor(n int) int { return (paddingUnit - n%paddingUnit) % paddingUnit }

func frame(m *Message, body []byte) []byte {
	return assemble(m, body, HeaderLen+len(body))
}

// assemble builds a wire message containing exactly body, but stamps the
// header Length field with declaredLen. Used for integrity/fingerprint
// computation where the declared length leads the bytes actually present.
func assemble(m *Message, body []byte, declaredLen int) []byte {
	out := make([]byte, HeaderLen+len(body))
	binary.BigEndian.PutUint16(out[0:2], uint16(m.Type))
	binary.BigEndian.PutUint16(out[2:4], uint16(declaredLen-HeaderLen))
	binary.BigEndian.PutUint32(out[4:8], MagicCookie)
	copy(out[8:HeaderLen], m.TxID[:])
	copy(out[HeaderLen:], body)
	return out
}

// frameWithLength kept for readability at integrity call sites.
func frameWithLength(m *Message, body []byte, declaredLen int) []byte {
	return assemble(m, body, declaredLen)
}

// Decode parses a STUN datagram. When key is non-nil and the message carries
// MESSAGE-INTEGRITY the HMAC is validated in constant time; a mismatch is a
// stunerror.KindIntegrity error. Unknown comprehension-required attributes
// produce *UnknownRequiredError (also integrity-class) carrying their types.
func Decode(data, key []byte) (*Message, error) {
	if len(data) < HeaderLen {
		return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrShortMessage.Error(), ErrShortMessage)
	}
	if data[0]&0xC0 != 0 {
		return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrLeadingBits.Error(), ErrLeadingBits)
	}
	msgType := MessageType(binary.BigEndian.Uint16(data[0:2]))
	if !msgType.IsKnown() {
		return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrUnknownMethod.Error()+": 0x"+hex16(uint16(msgType)), ErrUnknownMethod)
	}
	if binary.BigEndian.Uint32(data[4:8]) != MagicCookie {
		return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrBadCookie.Error(), ErrBadCookie)
	}
	declared := int(binary.BigEndian.Uint16(data[2:4]))
	if declared != len(data)-HeaderLen {
		return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrBadLength.Error(), ErrBadLength)
	}

	var txID TransactionID
	copy(txID[:], data[8:HeaderLen])
	m := &Message{Type: msgType, TxID: txID}

	var (
		unknownRequired []AttrType
		attrs           []Attribute
		miStart         = -1 // wire offset where MESSAGE-INTEGRITY begins
		miEnd           = -1 // wire offset just past its value
		fpStart         = -1 // wire offset where FINGERPRINT begins
		seenIntegrity   bool
	)

	p := HeaderLen
	end := len(data)
	for p < end {
		if end-p < attrHeaderLen {
			return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrTruncatedAttr.Error(), ErrTruncatedAttr)
		}
		at := AttrType(binary.BigEndian.Uint16(data[p : p+2]))
		alen := int(binary.BigEndian.Uint16(data[p+2 : p+4]))
		vStart := p + attrHeaderLen
		vEnd := vStart + alen
		if vEnd > end {
			return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrTruncatedAttr.Error(), ErrTruncatedAttr)
		}
		if seenIntegrity && at != AttrFingerprint {
			return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrAttrAfterIntegrity.Error(), ErrAttrAfterIntegrity)
		}
		value := make([]byte, alen)
		copy(value, data[vStart:vEnd])
		attrs = append(attrs, Attribute{Type: at, Value: value})

		switch {
		case at == AttrMessageIntegrity:
			if alen != integrityLen {
				return nil, stunerror.New(stunerror.KindInput, "decode", "MESSAGE-INTEGRITY must be 20 bytes")
			}
			seenIntegrity = true
			miStart, miEnd = p, vEnd
		case at == AttrFingerprint:
			if alen != fingerprintLen {
				return nil, stunerror.New(stunerror.KindInput, "decode", "FINGERPRINT must be 4 bytes")
			}
			fpStart = p
		case at.Required() && !isKnownRequired(at):
			unknownRequired = append(unknownRequired, at)
		}

		p = vEnd + paddingFor(alen)
		if p > end {
			return nil, stunerror.Wrap(stunerror.KindInput, "decode", ErrTruncatedAttr.Error(), ErrTruncatedAttr)
		}
	}

	m.Attrs = attrs

	if len(unknownRequired) > 0 {
		return nil, &UnknownRequiredError{TxID: txID, Unknown: unknownRequired}
	}

	if seenIntegrity {
		if key == nil {
			return nil, stunerror.New(stunerror.KindCompute, "decode", "integrity attribute present but no verification key supplied")
		}
		// HMAC input = bytes before MI, Length rewritten to end right after MI.
		input := append([]byte(nil), data[:miStart]...)
		binary.BigEndian.PutUint16(input[2:4], uint16(miEnd-HeaderLen))
		wantMAC := attrs[len(attrs)-1].Value // MI value saved below for fp case
		if fpAttr, hasFP := m.Get(AttrFingerprint); hasFP {
			// MI is not the last attr when FINGERPRINT follows; locate it.
			for _, a := range attrs {
				if a.Type == AttrMessageIntegrity {
					wantMAC = a.Value
					break
				}
			}
			if !hmac.Equal(hmacSHA1(key, input), wantMAC) {
				return nil, stunerror.Wrap(stunerror.KindIntegrity, "decode", ErrIntegrityMismatch.Error(), ErrIntegrityMismatch)
			}
			m.IntegrityOK = true
			fpInput := append([]byte(nil), data[:fpStart]...)
			binary.BigEndian.PutUint16(fpInput[2:4], uint16(fpStart+attrHeaderLen+fingerprintLen-HeaderLen))
			wantCRC := binary.BigEndian.Uint32(fpAttr.Value)
			gotCRC := crc32.ChecksumIEEE(fpInput) ^ fingerprintXOR
			if wantCRC != gotCRC {
				return nil, stunerror.Wrap(stunerror.KindIntegrity, "decode", ErrFingerprintMismatch.Error(), ErrFingerprintMismatch)
			}
			return m, nil
		}
		if !hmac.Equal(hmacSHA1(key, input), wantMAC) {
			return nil, stunerror.Wrap(stunerror.KindIntegrity, "decode", ErrIntegrityMismatch.Error(), ErrIntegrityMismatch)
		}
		m.IntegrityOK = true
	}

	return m, nil
}

func isKnownRequired(t AttrType) bool {
	switch t {
	case AttrMappedAddress, AttrUsername, AttrMessageIntegrity, AttrErrorCode,
		AttrUnknownAttrs, AttrRealm, AttrNonce, AttrXORMappedAddress,
		AttrPriority, AttrUseCandidate:
		return true
	}
	return false
}

// UnknownRequiredError reports comprehension-required attributes the receiver
// does not understand (server answers with 420 + UNKNOWN-ATTRIBUTES).
type UnknownRequiredError struct {
	TxID    TransactionID
	Unknown []AttrType
}

func (e *UnknownRequiredError) Error() string {
	return "stun.decode: integrity: unknown comprehension-required attribute(s)"
}

// Kind categorizes the rejection as integrity-class per stunerror.
func (e *UnknownRequiredError) Kind() stunerror.Kind { return stunerror.KindIntegrity }

// AttrTypes returns the offending attribute types.
func (e *UnknownRequiredError) AttrTypes() []AttrType { return e.Unknown }

func hmacSHA1(key, msg []byte) []byte {
	h := hmac.New(sha1.New, key)
	h.Write(msg)
	return h.Sum(nil)
}

func hex16(v uint16) string {
	const hexd = "0123456789abcdef"
	out := make([]byte, 4)
	for i := 3; i >= 0; i-- {
		out[i] = hexd[v&0x0F]
		v >>= 4
	}
	return string(out)
}

// MarshalXORAddressValue returns the on-wire VALUE bytes of an
// XOR-MAPPED-ADDRESS attribute (RFC 5389 §15.2). Exported for fixture and
// cross-implementation testing.
func MarshalXORAddressValue(ip net.IP, port int, txID TransactionID) ([]byte, error) {
	return encodeXORAddress(ip, port, txID)
}

// UnmarshalXORAddressValue parses an XOR-MAPPED-ADDRESS value.
func UnmarshalXORAddressValue(v []byte, txID TransactionID) (net.IP, int, error) {
	return decodeXORAddress(v, txID)
}

// encodeXORAddress builds an XOR-MAPPED-ADDRESS value (RFC 5389 §15.2).
func encodeXORAddress(ip net.IP, port int, txID TransactionID) ([]byte, error) {
	if v4 := ip.To4(); v4 != nil {
		out := make([]byte, 8)
		out[1] = FamilyIPv4
		binary.BigEndian.PutUint16(out[2:4], uint16(port)^uint16(MagicCookie>>16))
		for i := 0; i < 4; i++ {
			out[4+i] = v4[i] ^ cookieByte(i)
		}
		return out, nil
	}
	v6 := ip.To16()
	if v6 == nil {
		return nil, stunerror.New(stunerror.KindInput, "xoraddr", "neither IPv4 nor IPv6 address")
	}
	out := make([]byte, 20)
	out[1] = FamilyIPv6
	binary.BigEndian.PutUint16(out[2:4], uint16(port)^uint16(MagicCookie>>16))
	for i := 0; i < 16; i++ {
		out[4+i] = v6[i] ^ xorMaskByte(i, txID)
	}
	return out, nil
}

// decodeXORAddress parses an XOR-MAPPED-ADDRESS value.
func decodeXORAddress(v []byte, txID TransactionID) (net.IP, int, error) {
	if len(v) < 4 {
		return nil, 0, stunerror.New(stunerror.KindInput, "xoraddr", "address value too short")
	}
	if v[0] != 0 {
		return nil, 0, stunerror.New(stunerror.KindInput, "xoraddr", "reserved byte must be zero")
	}
	port := int(binary.BigEndian.Uint16(v[2:4]) ^ uint16(MagicCookie>>16))
	switch v[1] {
	case FamilyIPv4:
		if len(v) != 8 {
			return nil, 0, stunerror.New(stunerror.KindInput, "xoraddr", "IPv4 XOR address must be 8 bytes")
		}
		ip := make(net.IP, 4)
		for i := 0; i < 4; i++ {
			ip[i] = v[4+i] ^ cookieByte(i)
		}
		return ip, port, nil
	case FamilyIPv6:
		if len(v) != 20 {
			return nil, 0, stunerror.New(stunerror.KindInput, "xoraddr", "IPv6 XOR address must be 20 bytes")
		}
		ip := make(net.IP, 16)
		for i := 0; i < 16; i++ {
			ip[i] = v[4+i] ^ xorMaskByte(i, txID)
		}
		return ip, port, nil
	default:
		return nil, 0, stunerror.New(stunerror.KindInput, "xoraddr", "unknown address family")
	}
}

// cookieByte returns big-endian byte i of the magic cookie.
func cookieByte(i int) byte { return byte(MagicCookie >> (24 - 8*uint(i))) }

// xorMaskByte returns byte i of the 16-byte XOR mask: cookie || transaction id.
func xorMaskByte(i int, txID TransactionID) byte {
	if i < 4 {
		return cookieByte(i)
	}
	return txID[i-4]
}
