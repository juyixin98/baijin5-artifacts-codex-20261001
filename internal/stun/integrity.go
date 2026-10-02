package stun

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/binary"
	"fmt"
)

// IntegrityLen is the HMAC-SHA1 length carried by MESSAGE-INTEGRITY (RFC 5389
// 15.4). The lab uses STUN's short-term credential style: both sides share one
// symmetric key out of band. This is sufficient for controlled UDP testing and
// is NOT the long-term USERAUTH/REALM/NONCE mechanism.
const IntegrityLen = 20

// AddMessageIntegrity serialises method/class/txnID/attrs and appends a
// MESSAGE-INTEGRITY attribute. The HMAC input is the whole message up to the
// end of that attribute, with the header length field set to cover the
// attribute and with a 24-byte placeholder (type, length=20, zeroed value).
func AddMessageIntegrity(method Method, class Class, txnID TransactionID, attrs []Attribute, key []byte) ([]byte, error) {
	const op = "AddMessageIntegrity"
	if len(key) == 0 {
		return nil, fail(KindCompute, op, "empty integrity key")
	}
	body, err := EncodeAttributes(attrs)
	if err != nil {
		return nil, err
	}
	totalBody := len(body) + 4 + IntegrityLen
	buf := make([]byte, HeaderSize+totalBody)
	binary.BigEndian.PutUint16(buf[0:2], EncodeType(method, class))
	binary.BigEndian.PutUint16(buf[2:4], uint16(totalBody))
	binary.BigEndian.PutUint32(buf[4:8], MagicCookie)
	copy(buf[8:20], txnID[:])
	copy(buf[HeaderSize:], body)
	miOff := HeaderSize + len(body)
	binary.BigEndian.PutUint16(buf[miOff:miOff+2], uint16(AttrMessageIntegrity))
	binary.BigEndian.PutUint16(buf[miOff+2:miOff+4], IntegrityLen)
	mac := hmac.New(sha1.New, key)
	if _, err := mac.Write(buf[:miOff+4+IntegrityLen]); err != nil {
		return nil, &Error{Kind: KindCompute, Op: op, Detail: "hmac write failed", Err: err}
	}
	copy(buf[miOff+4:miOff+4+IntegrityLen], mac.Sum(nil))
	return buf, nil
}

// VerifyMessageIntegrity checks MESSAGE-INTEGRITY on a raw datagram. The
// attribute must be present, well formed (value length 20) and the final
// attribute in the message (FINGERPRINT is outside this lab's scope).
func VerifyMessageIntegrity(raw, key []byte) error {
	const op = "VerifyMessageIntegrity"
	if len(key) == 0 {
		return fail(KindCompute, op, "empty integrity key")
	}
	if len(raw) < HeaderSize {
		return fail(KindInput, op, "datagram shorter than header")
	}
	off := HeaderSize
	miStart := -1
	for off < len(raw) {
		if len(raw)-off < 4 {
			return fail(KindIntegrity, op, "attribute block truncated before MESSAGE-INTEGRITY")
		}
		t := AttributeType(binary.BigEndian.Uint16(raw[off : off+2]))
		vlen := int(binary.BigEndian.Uint16(raw[off+2 : off+4]))
		attrEnd := off + 4 + vlen
		if attrEnd > len(raw) {
			return failAttr(KindIntegrity, op, t,
				fmt.Sprintf("attribute length %d overruns datagram", vlen))
		}
		next := attrEnd
		if pad := (4 - vlen%4) % 4; pad > 0 {
			next += pad
		}
		if next > len(raw) {
			return failAttr(KindIntegrity, op, t, "attribute padding overruns datagram")
		}
		if t == AttrMessageIntegrity {
			if vlen != IntegrityLen {
				return failAttr(KindIntegrity, op, AttrMessageIntegrity,
					fmt.Sprintf("MESSAGE-INTEGRITY value length must be 20, got %d", vlen))
			}
			if next != len(raw) {
				return failAttr(KindIntegrity, op, AttrMessageIntegrity,
					"MESSAGE-INTEGRITY must be the last attribute")
			}
			miStart = off
			break
		}
		off = next
	}
	if miStart < 0 {
		return failAttr(KindIntegrity, op, AttrMessageIntegrity,
			"MESSAGE-INTEGRITY attribute absent")
	}

	coveredLen := miStart + 4 + IntegrityLen
	expected := make([]byte, coveredLen)
	copy(expected, raw[:coveredLen])
	binary.BigEndian.PutUint16(expected[2:4], uint16(coveredLen-HeaderSize))
	// The 20-byte MESSAGE-INTEGRITY value is part of the HMAC input as a zero
	// placeholder: the tag cannot cover itself (RFC 5389 15.4). The sender
	// (and the independent Python oracle) computes the tag over zeros here.
	for i := miStart + 4; i < coveredLen; i++ {
		expected[i] = 0
	}
	mac := hmac.New(sha1.New, key)
	if _, err := mac.Write(expected); err != nil {
		return &Error{Kind: KindCompute, Op: op, Detail: "hmac write failed", Err: err}
	}
	want := mac.Sum(nil)
	got := raw[miStart+4 : miStart+4+IntegrityLen]
	if !hmac.Equal(got, want) {
		return failAttr(KindIntegrity, op, AttrMessageIntegrity,
			"HMAC mismatch: message tampered or wrong key")
	}
	return nil
}
