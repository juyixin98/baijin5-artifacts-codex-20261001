package stun

import (
	"encoding/binary"
	"fmt"
	"net"
)

// MaxAttributeValueLen bounds a single STUN attribute value (16-bit length).
const MaxAttributeValueLen = 0xFFFF

// Attribute is a raw STUN TLV attribute as carried inside a message. Value is
// the unpadded payload; on-wire padding is handled by Encode/Decode.
type Attribute struct {
	Type  AttributeType
	Value []byte
}

// paddedLen returns the on-wire length including 4-byte padding.
func paddedLen(n int) int { return (n + 3) &^ 3 }

// EncodeAttributes serialises attributes back to back:
//
//	0                   2                   4
//	+---------------------+---------------------+
//	|         Type        |        Length       |
//	+---------------------+---------------------+
//	|        Value (Length bytes) ...
//	+------------------------------------------+
//	|               padding (0-3 bytes)
//
// The length field carries the UNPADDED value length; padding bytes are zero.
func EncodeAttributes(attrs []Attribute) ([]byte, error) {
	total := 0
	for _, a := range attrs {
		if len(a.Value) > MaxAttributeValueLen {
			return nil, failAttr(KindInput, "EncodeAttributes", a.Type,
				fmt.Sprintf("attribute value too long: %d", len(a.Value)))
		}
		total += 4 + paddedLen(len(a.Value))
	}
	buf := make([]byte, 0, total)
	for _, a := range attrs {
		hdr := [4]byte{}
		binary.BigEndian.PutUint16(hdr[0:2], uint16(a.Type))
		binary.BigEndian.PutUint16(hdr[2:4], uint16(len(a.Value)))
		buf = append(buf, hdr[:]...)
		buf = append(buf, a.Value...)
		if pad := paddedLen(len(a.Value)) - len(a.Value); pad > 0 {
			buf = append(buf, make([]byte, pad)...)
		}
	}
	return buf, nil
}

// DecodeAttributes parses every TLV from body. It performs structural
// validation only: truncated attributes, oversized lengths, and trailing
// garbage are reported as KindInput. Attribute semantics are left to callers.
func DecodeAttributes(body []byte) ([]Attribute, error) {
	const op = "DecodeAttributes"
	var attrs []Attribute
	for off := 0; off < len(body); {
		if len(body)-off < 4 {
			return nil, fail(KindInput, op,
				fmt.Sprintf("truncated attribute header at offset %d: %d bytes left", off, len(body)-off))
		}
		t := AttributeType(binary.BigEndian.Uint16(body[off : off+2]))
		vlen := int(binary.BigEndian.Uint16(body[off+2 : off+4]))
		start := off + 4
		end := start + vlen
		if end > len(body) {
			return nil, failAttr(KindInput, op, t,
				fmt.Sprintf("length %d overruns message body (%d bytes total)", vlen, len(body)))
		}
		val := make([]byte, vlen)
		copy(val, body[start:end])
		attrs = append(attrs, Attribute{Type: t, Value: val})
		off = end
		if pad := paddedLen(vlen) - vlen; pad > 0 {
			if off+pad > len(body) {
				// Padding runs past the end: malformed rather than silently
				// accepted, because the declared message length excludes it.
				return nil, failAttr(KindInput, op, t,
					"attribute padding overruns message body")
			}
			off += pad
		}
	}
	return attrs, nil
}

// Address is a STUN transport endpoint from MAPPED-ADDRESS families.
type Address struct {
	IP   net.IP
	Port int
}

const (
	familyIPv4 = 0x01
	familyIPv6 = 0x02
)

// EncodeMappedAddress serialises a MAPPED-ADDRESS attribute value (RFC 5389
// 15.1): one reserved zero byte, address family, 16-bit port, then the
// un-XORed address.
func EncodeMappedAddress(a Address) ([]byte, error) {
	const op = "EncodeMappedAddress"
	v4 := a.IP.To4()
	if v4 != nil {
		buf := make([]byte, 8)
		buf[1] = familyIPv4
		binary.BigEndian.PutUint16(buf[2:4], uint16(a.Port))
		copy(buf[4:8], v4)
		return buf, nil
	}
	if ip6 := a.IP.To16(); ip6 != nil {
		buf := make([]byte, 20)
		buf[1] = familyIPv6
		binary.BigEndian.PutUint16(buf[2:4], uint16(a.Port))
		copy(buf[4:20], ip6)
		return buf, nil
	}
	return nil, fail(KindInput, op, "invalid IP address")
}

// DecodeMappedAddress parses a MAPPED-ADDRESS value.
func DecodeMappedAddress(v []byte) (Address, error) {
	const op = "DecodeMappedAddress"
	if len(v) < 4 {
		return Address{}, fail(KindInput, op, "value shorter than 4 bytes")
	}
	if v[0] != 0 {
		return Address{}, fail(KindInput, op,
			fmt.Sprintf("reserved byte must be zero, got 0x%02x", v[0]))
	}
	return decodeHostPort(v[1], v[2:4], v[4:], op)
}

// EncodeXORMappedAddress serialises an XOR-MAPPED-ADDRESS attribute value
// (RFC 5389 15.2). The port is XORed with the top 16 bits of the magic cookie;
// an IPv4 address with the full cookie; an IPv6 address with the 16-byte
// cookie||transaction-id block.
func EncodeXORMappedAddress(a Address, txnID TransactionID) ([]byte, error) {
	const op = "EncodeXORMappedAddress"
	buf := make([]byte, 0, 20)
	if v4 := a.IP.To4(); v4 != nil {
		buf = append(buf, 0, familyIPv4)
		p := make([]byte, 2)
		binary.BigEndian.PutUint16(p, uint16(a.Port)^uint16(MagicCookie>>16))
		buf = append(buf, p...)
		mask := make([]byte, 4)
		binary.BigEndian.PutUint32(mask, MagicCookie)
		ip := make([]byte, 4)
		for i := range v4 {
			ip[i] = v4[i] ^ mask[i]
		}
		buf = append(buf, ip...)
		return buf, nil
	}
	if ip6 := a.IP.To16(); ip6 != nil {
		buf = append(buf, 0, familyIPv6)
		p := make([]byte, 2)
		binary.BigEndian.PutUint16(p, uint16(a.Port)^uint16(MagicCookie>>16))
		buf = append(buf, p...)
		mask := make([]byte, 16)
		binary.BigEndian.PutUint32(mask[0:4], MagicCookie)
		copy(mask[4:16], txnID[:])
		ip := make([]byte, 16)
		for i := range ip6 {
			ip[i] = ip6[i] ^ mask[i]
		}
		buf = append(buf, ip...)
		return buf, nil
	}
	return nil, fail(KindInput, op, "invalid IP address")
}

// DecodeXORMappedAddress parses an XOR-MAPPED-ADDRESS value and un-XORs it
// with the magic cookie and the carrying message's transaction id.
func DecodeXORMappedAddress(v []byte, txnID TransactionID) (Address, error) {
	const op = "DecodeXORMappedAddress"
	if len(v) < 4 {
		return Address{}, fail(KindInput, op, "value shorter than 4 bytes")
	}
	if v[0] != 0 {
		return Address{}, fail(KindInput, op,
			fmt.Sprintf("reserved byte must be zero, got 0x%02x", v[0]))
	}
	addr, err := decodeHostPort(v[1], v[2:4], v[4:], op)
	if err != nil {
		return Address{}, err
	}
	addr.Port ^= int(MagicCookie >> 16)
	if v[1] == familyIPv4 {
		mask := make([]byte, 4)
		binary.BigEndian.PutUint32(mask, MagicCookie)
		for i := range addr.IP {
			addr.IP[i] ^= mask[i]
		}
		return addr, nil
	}
	mask := make([]byte, 16)
	binary.BigEndian.PutUint32(mask[0:4], MagicCookie)
	copy(mask[4:16], txnID[:])
	for i := range addr.IP {
		addr.IP[i] ^= mask[i]
	}
	return addr, nil
}

func decodeHostPort(family byte, port, ipBytes []byte, op string) (Address, error) {
	if len(port) < 2 {
		return Address{}, fail(KindInput, op, "truncated port field")
	}
	switch family {
	case familyIPv4:
		if len(ipBytes) != 4 {
			return Address{}, fail(KindInput, op,
				fmt.Sprintf("IPv4 family requires 4 address bytes, got %d", len(ipBytes)))
		}
		ip := make(net.IP, 4)
		copy(ip, ipBytes)
		return Address{IP: ip, Port: int(binary.BigEndian.Uint16(port))}, nil
	case familyIPv6:
		if len(ipBytes) != 16 {
			return Address{}, fail(KindInput, op,
				fmt.Sprintf("IPv6 family requires 16 address bytes, got %d", len(ipBytes)))
		}
		ip := make(net.IP, 16)
		copy(ip, ipBytes)
		return Address{IP: ip, Port: int(binary.BigEndian.Uint16(port))}, nil
	default:
		return Address{}, fail(KindInput, op, fmt.Sprintf("unknown address family 0x%02x", family))
	}
}

// ErrorCode is a STUN ERROR-CODE attribute (RFC 5389 15.6): class (hundreds,
// 3-6), number (0-99) and a UTF-8 reason phrase (may be empty).
type ErrorCode struct {
	Code   int
	Reason string
}

// EncodeErrorCode serialises an ERROR-CODE value.
func EncodeErrorCode(ec ErrorCode) ([]byte, error) {
	const op = "EncodeErrorCode"
	if ec.Code < 300 || ec.Code > 699 {
		return nil, fail(KindInput, op, fmt.Sprintf("error code out of range: %d", ec.Code))
	}
	if len(ec.Reason) > 128 { // value limit is 255-4 total
		return nil, fail(KindInput, op, "reason phrase too long")
	}
	buf := make([]byte, 4, 4+len(ec.Reason))
	buf[2] = byte(ec.Code / 100)
	buf[3] = byte(ec.Code % 100)
	buf = append(buf, []byte(ec.Reason)...)
	return buf, nil
}

// DecodeErrorCode parses an ERROR-CODE value, ignoring bits 3-0 of byte 2.
func DecodeErrorCode(v []byte) (ErrorCode, error) {
	const op = "DecodeErrorCode"
	if len(v) < 4 {
		return ErrorCode{}, fail(KindInput, op, "value shorter than 4 bytes")
	}
	cls := int(v[2] & 0x07)
	num := int(v[3])
	if cls < 3 || cls > 6 || num > 99 {
		return ErrorCode{}, fail(KindInput, op,
			fmt.Sprintf("invalid error code components: class=%d number=%d", cls, num))
	}
	return ErrorCode{Code: cls*100 + num, Reason: string(v[4:])}, nil
}

// EncodeUnknownAttributes serialises the 16-bit unknown attribute type list
// carried in a 420 response.
func EncodeUnknownAttributes(types []AttributeType) []byte {
	buf := make([]byte, 0, 2*len(types))
	for _, t := range types {
		b := make([]byte, 2)
		binary.BigEndian.PutUint16(b, uint16(t))
		buf = append(buf, b...)
	}
	return buf
}

// DecodeUnknownAttributes parses the UNKNOWN-ATTRIBUTES value. A truncated
// final 16-bit element is an input error.
func DecodeUnknownAttributes(v []byte) ([]AttributeType, error) {
	const op = "DecodeUnknownAttributes"
	if len(v)%2 != 0 {
		return nil, fail(KindInput, op, "odd-length unknown-attributes value")
	}
	out := make([]AttributeType, 0, len(v)/2)
	for len(v) > 0 {
		out = append(out, AttributeType(binary.BigEndian.Uint16(v[:2])))
		v = v[2:]
	}
	return out, nil
}
