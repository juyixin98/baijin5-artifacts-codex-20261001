package stun

import (
	"encoding/hex"
	"net"
	"strings"
	"testing"
)

// RFC 5769 section 2.3/2.4 fixed XOR-MAPPED-ADDRESS vectors, used here as
// external known-answer fixtures (they are defined by the RFC, not by this
// implementation).
var rfcTxn = TransactionID{0xb7, 0xe7, 0xa7, 0x01, 0xbc, 0x34, 0xd6, 0x86, 0xfa, 0x87, 0xdf, 0xae}

func TestRFC5769XORMappedVectors(t *testing.T) {
	cases := []struct {
		name     string
		encoded  string // attribute value only (after type+length), hex
		wantIP   string
		wantPort int
	}{
		{
			"ipv4 192.0.2.1:32853",
			"0001a147e112a643",
			"192.0.2.1", 32853,
		},
		{
			"ipv6 2001:db8:1234:5678:11:2233:4455:6677:32853",
			"0002a1470113a9faa5d3f179bc25f4b5bed2b9d9",
			"2001:db8:1234:5678:11:2233:4455:6677", 32853,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			raw, _ := hex.DecodeString(tc.encoded)
			got, err := DecodeXORMappedAddress(raw, rfcTxn)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			if got.Port != tc.wantPort || !got.IP.Equal(net.ParseIP(tc.wantIP)) {
				t.Fatalf("decoded = %s:%d, want %s:%d", got.IP, got.Port, tc.wantIP, tc.wantPort)
			}
			// Re-encode and expect byte-identical RFC bytes.
			out, err := EncodeXORMappedAddress(Address{IP: net.ParseIP(tc.wantIP), Port: tc.wantPort}, rfcTxn)
			if err != nil {
				t.Fatalf("encode: %v", err)
			}
			if hex.EncodeToString(out) != tc.encoded {
				t.Fatalf("re-encode = %x, want %s", out, tc.encoded)
			}
		})
	}
}

func TestAttributePaddingRoundTrip(t *testing.T) {
	// Values of every length 1..8 exercise all padding residues; an unknown
	// private type keeps them comprehension-optional for generic parsing.
	for n := 1; n <= 8; n++ {
		v := make([]byte, n)
		for i := range v {
			v[i] = byte(i + 1)
		}
		attrs := []Attribute{{Type: AttrSoftware, Value: v}}
		body, err := EncodeAttributes(attrs)
		if err != nil {
			t.Fatalf("n=%d encode: %v", n, err)
		}
		wantWire := 4 + ((n + 3) &^ 3)
		if len(body) != wantWire {
			t.Fatalf("n=%d wire length = %d, want %d", n, len(body), wantWire)
		}
		got, err := DecodeAttributes(body)
		if err != nil {
			t.Fatalf("n=%d decode: %v", n, err)
		}
		if len(got) != 1 || len(got[0].Value) != n {
			t.Fatalf("n=%d decoded mismatch: %+v", n, got)
		}
		for i := 0; i < n; i++ {
			if got[0].Value[i] != v[i] {
				t.Fatalf("n=%d value byte %d corrupted", n, i)
			}
		}
	}
}

func TestDecodeAttributesRejectsMalformed(t *testing.T) {
	cases := []struct {
		name string
		hex  string
		want ErrorKind
	}{
		{"truncated header", "0020", KindInput},
		{"length overruns body", "002000080001a147", KindInput},
		{"padding overruns body", "80220003010203", KindInput}, // 3-byte value needs 1 pad byte, none present
		{"trailing half word", "80220000010203", KindInput},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b, _ := hex.DecodeString(tc.hex)
			_, err := DecodeAttributes(b)
			if ErrorOf(err) != tc.want {
				t.Fatalf("got %v, want kind %s", err, tc.want)
			}
		})
	}
}

func TestMappedAddressRoundTrip(t *testing.T) {
	addrs := []Address{
		{IP: net.ParseIP("198.51.100.7"), Port: 40960},
		{IP: net.ParseIP("2001:db8::1"), Port: 53},
	}
	for _, a := range addrs {
		v, err := EncodeMappedAddress(a)
		if err != nil {
			t.Fatal(err)
		}
		got, err := DecodeMappedAddress(v)
		if err != nil {
			t.Fatal(err)
		}
		if !got.IP.Equal(a.IP) || got.Port != a.Port {
			t.Fatalf("round trip = %s:%d, want %s:%d", got.IP, got.Port, a.IP, a.Port)
		}
	}
}

func TestMappedAddressReservedByteAndFamily(t *testing.T) {
	// Non-zero reserved byte must be rejected.
	bad := []byte{0x01, 0x01, 0x00, 0x50, 1, 2, 3, 4}
	if _, err := DecodeMappedAddress(bad); ErrorOf(err) != KindInput {
		t.Fatalf("reserved byte accepted: %v", err)
	}
	// IPv4 family with 6 address bytes must be rejected.
	bad = []byte{0x00, 0x01, 0x00, 0x50, 1, 2, 3, 4, 5, 6}
	if _, err := DecodeMappedAddress(bad); ErrorOf(err) != KindInput {
		t.Fatalf("bad v4 length accepted: %v", err)
	}
	// Unknown family.
	bad = []byte{0x00, 0x09, 0x00, 0x50, 1, 2, 3, 4}
	if _, err := DecodeMappedAddress(bad); ErrorOf(err) != KindInput {
		t.Fatalf("unknown family accepted: %v", err)
	}
}

func TestErrorCodeRoundTrip(t *testing.T) {
	ec := ErrorCode{Code: 420, Reason: "Unknown Attribute"}
	v, err := EncodeErrorCode(ec)
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodeErrorCode(v)
	if err != nil || got.Code != 420 || got.Reason != "Unknown Attribute" {
		t.Fatalf("round trip = %+v err=%v", got, err)
	}
	// High bits of the class byte must be ignored.
	v[2] |= 0xF8
	got, err = DecodeErrorCode(v)
	if err != nil || got.Code != 420 {
		t.Fatalf("class mask handling wrong: %+v err=%v", got, err)
	}
}

func TestErrorCodeRejectsOutOfRange(t *testing.T) {
	for _, code := range []int{299, 700, 100} {
		if _, err := EncodeErrorCode(ErrorCode{Code: code}); ErrorOf(err) != KindInput {
			t.Fatalf("code %d accepted", code)
		}
	}
	bad, _ := hex.DecodeString("00000764") // class 7, number 100
	if _, err := DecodeErrorCode(bad); ErrorOf(err) != KindInput {
		t.Fatalf("invalid components accepted: %v", err)
	}
}

func TestUnknownAttributesRoundTrip(t *testing.T) {
	types := []AttributeType{0x0099, 0x7777, 0x0001}
	v := EncodeUnknownAttributes(types)
	got, err := DecodeUnknownAttributes(v)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 || got[0] != 0x0099 || got[1] != 0x7777 || got[2] != 0x0001 {
		t.Fatalf("round trip = %v", got)
	}
	if _, err := DecodeUnknownAttributes([]byte{0x00}); ErrorOf(err) != KindInput {
		t.Fatalf("odd length accepted")
	}
}

func TestErrorStringsCarryAttrAndKind(t *testing.T) {
	err := failAttr(KindUnknownCritical, "OpX", AttrMessageIntegrity, "boom")
	s := err.Error()
	if !strings.Contains(s, string(KindUnknownCritical)) || !strings.Contains(s, "0x0008") {
		t.Fatalf("error string %q missing kind/attr", s)
	}
}
