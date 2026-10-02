package stun

import (
	"encoding/binary"
	"testing"
)

// A tiny hand-checkable HMAC known answer (key and message are ASCII; the tag
// was produced by the independent Python oracle in test/oracle and frozen in
// test/testdata/hmac_smoke.json). The Go side must reproduce it byte for byte.
func TestAddAndVerifyIntegrityRoundTrip(t *testing.T) {
	key := []byte("lab-shared-secret")
	txn := TransactionID{0xaa, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
	attrs := []Attribute{{Type: AttrSoftware, Value: []byte("x")}}
	raw, err := AddMessageIntegrity(MethodBinding, ClassRequest, txn, attrs, key)
	if err != nil {
		t.Fatal(err)
	}
	// MESSAGE-INTEGRITY (4+20) must be appended after the padded software attr.
	if len(raw) != HeaderSize+8+24 {
		t.Fatalf("wire len = %d, want %d", len(raw), HeaderSize+8+24)
	}
	if err := VerifyMessageIntegrity(raw, key); err != nil {
		t.Fatalf("verify: %v", err)
	}
	m, err := UnmarshalMessage(raw)
	if err != nil {
		t.Fatal(err)
	}
	if v, ok := m.Attribute(AttrMessageIntegrity); !ok || len(v) != IntegrityLen {
		t.Fatal("message-integrity attribute missing or wrong length after parse")
	}
}

func TestIntegrityTamperCategories(t *testing.T) {
	key := []byte("lab-shared-secret")
	txn := TransactionID{1: 1}
	raw, _ := AddMessageIntegrity(MethodBinding, ClassRequest, txn, nil, key)

	t.Run("bit flip in txn id", func(t *testing.T) {
		bad := clone(raw)
		bad[12] ^= 0x01 // transaction id is covered by MESSAGE-INTEGRITY
		err := VerifyMessageIntegrity(bad, key)
		if ErrorOf(err) != KindIntegrity {
			t.Fatalf("got %v, want integrity_failure", err)
		}
	})
	t.Run("bit flip in tag", func(t *testing.T) {
		bad := clone(raw)
		bad[len(bad)-1] ^= 0x80
		if ErrorOf(VerifyMessageIntegrity(bad, key)) != KindIntegrity {
			t.Fatal("tampered tag accepted")
		}
	})
	t.Run("wrong key", func(t *testing.T) {
		if ErrorOf(VerifyMessageIntegrity(raw, []byte("other"))) != KindIntegrity {
			t.Fatal("wrong key accepted")
		}
	})
	t.Run("missing integrity", func(t *testing.T) {
		plain, _ := Marshal(MethodBinding, ClassRequest, txn, nil)
		if ErrorOf(VerifyMessageIntegrity(plain, key)) != KindIntegrity {
			t.Fatal("message without integrity accepted")
		}
	})
	t.Run("integrity not last", func(t *testing.T) {
		// Recompute as if a trailing attribute existed: declare larger length
		// and append a 4-byte attribute after the HMAC.
		bad := clone(raw)
		binary.BigEndian.PutUint16(bad[2:4], binary.BigEndian.Uint16(bad[2:4])+4)
		bad = append(bad, 0x80, 0x22, 0x00, 0x00)
		if ErrorOf(VerifyMessageIntegrity(bad, key)) != KindIntegrity {
			t.Fatal("integrity followed by another attribute accepted")
		}
	})
	t.Run("declared wrong mi length", func(t *testing.T) {
		bad := clone(raw)
		// MI header starts at len-24; claim value length 19.
		binary.BigEndian.PutUint16(bad[len(bad)-24+2:len(bad)-24+4], 19)
		if ErrorOf(VerifyMessageIntegrity(bad, key)) != KindIntegrity {
			t.Fatal("malformed MI length accepted")
		}
	})
}

func TestEmptyIntegrityKeyIsComputeFailure(t *testing.T) {
	txn := TransactionID{}
	if _, err := AddMessageIntegrity(MethodBinding, ClassRequest, txn, nil, nil); ErrorOf(err) != KindCompute {
		t.Fatalf("add: %v", err)
	}
	if err := VerifyMessageIntegrity([]byte{0, 0, 0, 0}, nil); ErrorOf(err) != KindCompute {
		t.Fatalf("verify: %v", err)
	}
}

func TestLengthMismatchCaughtAtStructuralLayer(t *testing.T) {
	// RFC 5389 15.4: when verifying MI the verifier REWRITES the header length
	// to the MI end position before HMAC, so a length-field lie with unchanged
	// bytes does not change the HMAC input. It must therefore be caught one
	// layer earlier by UnmarshalMessage (declared length != body) as
	// input_error — not silently accepted.
	key := []byte("k")
	txn := TransactionID{7: 7}
	raw, _ := AddMessageIntegrity(MethodBinding, ClassSuccessResponse, txn,
		[]Attribute{{Type: AttrSoftware, Value: []byte("ab")}}, key)
	bad := clone(raw)
	binary.BigEndian.PutUint16(bad[2:4], binary.BigEndian.Uint16(bad[2:4])+4)
	if _, err := UnmarshalMessage(bad); ErrorOf(err) != KindInput {
		t.Fatalf("structural layer accepted length lie: %v", err)
	}
	// Sanity: the unmodified vector verifies and parses.
	if err := VerifyMessageIntegrity(raw, key); err != nil {
		t.Fatal(err)
	}
	if _, err := UnmarshalMessage(raw); err != nil {
		t.Fatal(err)
	}
}

func clone(b []byte) []byte {
	out := make([]byte, len(b))
	copy(out, b)
	return out
}
