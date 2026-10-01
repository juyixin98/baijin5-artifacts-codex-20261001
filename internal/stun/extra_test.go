package stun_test

import (
	"encoding/binary"
	"net"
	"testing"

	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

func TestMarshal_RejectsInvalidInputs(t *testing.T) {
	tx := stun.MustTransactionID()
	if _, err := stun.Marshal(stun.NewMessage(stun.MessageType(0x0002), tx), nil, false); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("unknown type kind=%s", stunerror.Of(err))
	}
	m := stun.NewMessage(stun.BindingRequest, tx)
	if _, err := stun.Marshal(m, nil, true); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("fingerprint without key kind=%s", stunerror.Of(err))
	}

	// Preset system attributes are refused.
	bad := stun.NewMessage(stun.BindingRequest, tx)
	bad.Add(stun.AttrMessageIntegrity, make([]byte, 20))
	if _, err := stun.Marshal(bad, []byte("k"), false); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("preset MI kind=%s", stunerror.Of(err))
	}
}

func TestDecode_AttrAfterIntegrity(t *testing.T) {
	key := []byte("kk")
	tx := stun.MustTransactionID()
	m := stun.NewMessage(stun.BindingResponse, tx)
	good, err := stun.Marshal(m, key, false)
	if err != nil {
		t.Fatal(err)
	}
	// Insert a SOFTWARE attribute AFTER MESSAGE-INTEGRITY by rebuilding: take
	// prefix before MI and append SOFTWARE, then re-frame with new length.
	miPos := indexAttr(good, stun.AttrMessageIntegrity)
	if miPos < 0 {
		t.Fatal("MI not found")
	}
	// Build body = attrs before MI (MI absent), then a SOFTWARE attribute.
	// Decoder must reject a non-FINGERPRINT attr appearing after MI seen...
	// here we instead append SOFTWARE then an MI header, so SOFTWARE legitimately
	// precedes MI; to force the violation, append a raw non-FP attr after an MI.
	var body []byte
	body = append(body, good[20:miPos]...)
	miAttr := make([]byte, 24)
	miAttr[0], miAttr[1] = 0x00, 0x08
	miAttr[2], miAttr[3] = 0x00, 0x14
	body = append(body, miAttr...)
	extra := make([]byte, 8)
	extra[0], extra[1] = 0x80, 0x22 // SOFTWARE (not FINGERPRINT) -> violation
	extra[2], extra[3] = 0x00, 0x04
	body = append(body, extra...)
	wire := directFrame(stun.BindingResponse, tx, body)
	if _, err := stun.Decode(wire, key); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("attr after MI kind=%s err=%v", stunerror.Of(err), err)
	}
}

func TestDecode_BadMIAndFPLengths(t *testing.T) {
	tx := stun.MustTransactionID()
	// MI value claimed as 19 bytes.
	mi := make([]byte, 4+19)
	mi[0], mi[1] = 0x00, 0x08
	mi[2], mi[3] = 0x00, 0x13
	wire := directFrame(stun.BindingResponse, tx, mi)
	if _, err := stun.Decode(wire, []byte("k")); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("bad MI len kind=%s", stunerror.Of(err))
	}
	// FP value claimed as 3 bytes.
	fp := make([]byte, 4+3)
	fp[0], fp[1] = 0x80, 0x28
	fp[2], fp[3] = 0x00, 0x03
	wire2 := directFrame(stun.BindingResponse, tx, fp)
	if _, err := stun.Decode(wire2, nil); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("bad FP len kind=%s", stunerror.Of(err))
	}
}

func TestXORAddress_BadFamiliesAndLengths(t *testing.T) {
	tx := stun.MustTransactionID()
	if err := (&stun.Message{}).AddXORMappedAddress(nil, 1); stunerror.Of(err) != stunerror.KindInput {
		t.Fatalf("nil ip kind=%s", stunerror.Of(err))
	}
	bad := [][]byte{
		{0x00, 0x09, 0x00, 0x00, 0, 0, 0, 0}, // unknown family
		{0x01, 0x01, 0x00, 0x00, 0, 0, 0, 0}, // reserved byte set
		{0x00, 0x01, 0x00, 0x00, 0, 0, 0},    // ipv4 wrong length
		{0x00, 0x02, 0x00, 0x00},             // ipv6 too short
		{0x00},                               // very short
	}
	for i, v := range bad {
		if _, _, err := stun.UnmarshalXORAddressValue(v, tx); stunerror.Of(err) != stunerror.KindInput {
			t.Fatalf("case %d kind=%s", i, stunerror.Of(err))
		}
	}
}

func TestMessage_GetAddErrorCodeEdge(t *testing.T) {
	tx := stun.MustTransactionID()
	m := stun.NewMessage(stun.BindingError, tx)
	// ERROR-CODE present but shorter than 4 bytes: Get helper reports found
	// but no decoded code.
	m.Add(stun.AttrErrorCode, []byte{0, 0})
	wire, err := stun.Marshal(m, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	dec, err := stun.Decode(wire, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, ok := dec.ErrorCode(); ok {
		t.Fatal("short ERROR-CODE must not decode a code")
	}
	if _, ok := dec.Get(stun.AttrSoftware); ok {
		t.Fatal("absent attribute Get must report false")
	}
}

func TestDeriveLongTermKey_Deterministic(t *testing.T) {
	k1 := stun.DeriveLongTermKey("user", "realm", "pass")
	k2 := stun.DeriveLongTermKey("user", "realm", "pass")
	if len(k1) != 16 || !equalBytes(k1, k2) {
		t.Fatalf("long-term key must be 16-byte MD5 and deterministic: %x %x", k1, k2)
	}
	k3 := stun.DeriveLongTermKey("user", "realm", "other")
	if equalBytes(k1, k3) {
		t.Fatal("different passwords must yield different keys")
	}
}

func TestMessageHelpers_XORMappedMissing(t *testing.T) {
	m := stun.NewMessage(stun.BindingResponse, stun.MustTransactionID())
	ip, port, ok, err := m.XORMappedAddress()
	if ok || err != nil || ip != nil || port != 0 {
		t.Fatalf("missing XOR attr: ip=%v port=%d ok=%v err=%v", ip, port, ok, err)
	}
}

func indexAttr(wire []byte, at stun.AttrType) int {
	p := stun.HeaderLen
	for p < len(wire) {
		t := stun.AttrType(uint16(wire[p])<<8 | uint16(wire[p+1]))
		al := int(uint16(wire[p+2])<<8 | uint16(wire[p+3]))
		if t == at {
			return p
		}
		p += 4 + al + (4-al%4)%4
	}
	return -1
}

func directFrame(mt stun.MessageType, tx stun.TransactionID, body []byte) []byte {
	out := make([]byte, stun.HeaderLen+len(body))
	binary.BigEndian.PutUint16(out[0:2], uint16(mt))
	binary.BigEndian.PutUint16(out[2:4], uint16(len(body)))
	binary.BigEndian.PutUint32(out[4:8], stun.MagicCookie)
	copy(out[8:20], tx[:])
	copy(out[20:], body)
	return out
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

var _ = net.ParseIP
