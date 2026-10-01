package stun_test

import (
	"encoding/binary"
	"encoding/hex"
	"errors"
	"net"
	"testing"

	"localstun/internal/stun"
)

// netIP parses an IP literal, failing the test on error.
func netIP(s string) net.IP {
	ip := net.ParseIP(s)
	if ip == nil {
		panic("bad ip fixture: " + s)
	}
	return ip
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("bad hex %q: %v", s, err)
	}
	return b
}

// rawAttr frames one attribute with zero padding to four-byte alignment.
func rawAttr(at stun.AttrType, value []byte) []byte {
	pad := (4 - len(value)%4) % 4
	payload := make([]byte, len(value)+pad)
	copy(payload, value)
	return rawAttrN(at, payload, len(value))
}

// rawAttrN frames an attribute where the value length field is valueLen but
// the payload on the wire is valueAndPad (value + padding), enabling padding
// anomaly tests.
func rawAttrN(at stun.AttrType, valueAndPad []byte, valueLen int) []byte {
	out := make([]byte, 0, 4+len(valueAndPad))
	var hdr [4]byte
	binary.BigEndian.PutUint16(hdr[0:2], uint16(at))
	binary.BigEndian.PutUint16(hdr[2:4], uint16(valueLen))
	out = append(out, hdr[:]...)
	out = append(out, valueAndPad...)
	return out
}

// buildRaw assembles a complete STUN datagram directly from pre-framed
// attribute blobs, bypassing stun.Marshal so malformed/padding-anomaly inputs
// can be constructed. mi/fp are appended inside attrs by the caller.
func buildRaw(t *testing.T, mt stun.MessageType, txID stun.TransactionID, _ []byte, attrs ...[]byte) []byte {
	t.Helper()
	body := make([]byte, 0)
	for _, a := range attrs {
		body = append(body, a...)
	}
	out := make([]byte, stun.HeaderLen+len(body))
	binary.BigEndian.PutUint16(out[0:2], uint16(mt))
	binary.BigEndian.PutUint16(out[2:4], uint16(len(body)))
	binary.BigEndian.PutUint32(out[4:8], stun.MagicCookie)
	copy(out[8:20], txID[:])
	copy(out[20:], body)
	return out
}

func asError(err error, target interface{}) bool {
	return errors.As(err, target)
}

func errorIs(err, target error) bool { return errors.Is(err, target) }
