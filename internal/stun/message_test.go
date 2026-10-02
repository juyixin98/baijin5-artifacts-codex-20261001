package stun

import (
	"encoding/binary"
	"encoding/hex"
	"strings"
	"testing"
)

func TestMarshalUnmarshalRoundTrip(t *testing.T) {
	txn := TransactionID{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
	attrs := []Attribute{
		{Type: AttrSoftware, Value: []byte("lab")},     // 3 bytes -> 1 pad byte
		{Type: AttrUnknownAttributes, Value: []byte{}}, // zero-length
	}
	raw, err := Marshal(MethodBinding, ClassRequest, txn, attrs)
	if err != nil {
		t.Fatal(err)
	}
	if got := len(raw); got != HeaderSize+12 { // 4+4 padded + 4 zero-len
		t.Fatalf("wire len = %d, want %d", got, HeaderSize+12)
	}
	m, err := UnmarshalMessage(raw)
	if err != nil {
		t.Fatal(err)
	}
	if m.Method != MethodBinding || m.Class != ClassRequest || m.TransactionID != txn {
		t.Fatalf("header mismatch: %+v", m)
	}
	if string(m.Attributes[0].Value) != "lab" || len(m.Attributes[1].Value) != 0 {
		t.Fatalf("attributes mismatch: %+v", m.Attributes)
	}
}

func TestUnmarshalRejectsMalformed(t *testing.T) {
	good := func() []byte {
		txn := TransactionID{9: 0xab}
		b, _ := Marshal(MethodBinding, ClassRequest, txn, nil)
		return b
	}
	cases := []struct {
		name   string
		mutate func([]byte) []byte
	}{
		{"too short", func(b []byte) []byte { return b[:10] }},
		{"leading bits set", func(b []byte) []byte { b[0] |= 0x80; return b }},
		{"bad magic cookie", func(b []byte) []byte {
			binary.BigEndian.PutUint32(b[4:8], 0xdeadbeef)
			return b
		}},
		{"length smaller than body", func(b []byte) []byte {
			binary.BigEndian.PutUint16(b[2:4], 1)
			return append(b, 0)
		}},
		{"length larger than body", func(b []byte) []byte {
			binary.BigEndian.PutUint16(b[2:4], 4)
			return b
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := tc.mutate(good())
			_, err := UnmarshalMessage(b)
			if ErrorOf(err) != KindInput {
				t.Fatalf("got %v, want input_error", err)
			}
		})
	}
}

func TestUnknownRequiredDetection(t *testing.T) {
	attrs := []Attribute{
		{Type: AttrSoftware, Value: []byte("ok")},            // optional, ignored
		{Type: 0x0099, Value: []byte{1}},                     // unknown required
		{Type: 0x7777, Value: []byte{1, 2, 3}},               // unknown required
		{Type: AttrXORMappedAddress, Value: []byte{1, 2, 3}}, // known required
		{Type: 0x0099, Value: []byte{9}},                     // dup, reported once
	}
	understood := func(at AttributeType) bool { return at == AttrXORMappedAddress }
	got := UnknownRequired(attrs, understood)
	if len(got) != 2 || got[0] != 0x0099 || got[1] != 0x7777 {
		t.Fatalf("unknown required = %v, want [0x0099 0x7777] deduped in order", got)
	}
}

func TestNewTransactionIDUnique(t *testing.T) {
	seen := map[TransactionID]bool{}
	for i := 0; i < 1000; i++ {
		id, err := NewTransactionID()
		if err != nil {
			t.Fatal(err)
		}
		if seen[id] {
			t.Fatal("duplicate random transaction id")
		}
		seen[id] = true
	}
}

func TestUnmarshalEmptyBody(t *testing.T) {
	// Exact 20-byte request is valid.
	b, _ := hex.DecodeString("000100002112a4420102030405060708090a0b0c")
	m, err := UnmarshalMessage(b)
	if err != nil {
		t.Fatal(err)
	}
	if len(m.Attributes) != 0 || !strings.HasPrefix(hex.EncodeToString(m.Raw), "0001") {
		t.Fatalf("empty body parse wrong: %+v", m)
	}
}
