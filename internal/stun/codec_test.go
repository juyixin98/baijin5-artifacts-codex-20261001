package stun_test

import (
	"bytes"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

// rfcKey is the HMAC key of the RFC 5769 sections 2.1-2.3 vectors.
var rfcKey = []byte("VOkJxbRl1RmTxUk/WvJxBt")

// loadHexFixture reads a loose hex dump (whitespace separated; trailing ASCII
// comments tolerated) from the repository testdata directory.
func loadHexFixture(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "vectors", name))
	if err != nil {
		t.Fatalf("read fixture %s: %v", name, err)
	}
	var clean []byte
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		fields := strings.Fields(line)
		for i, f := range fields {
			// Fixture rows carry no ASCII annotation column; guard anyway.
			if len(f) != 2 {
				t.Fatalf("fixture %s field %d not a hex byte: %q", name, i, f)
			}
			b, err := hex.DecodeString(f)
			if err != nil {
				t.Fatalf("fixture %s bad hex %q: %v", name, f, err)
			}
			clean = append(clean, b...)
		}
	}
	return clean
}

func TestRFC5769_2_1_RequestKnownAnswer(t *testing.T) {
	wire := loadHexFixture(t, "rfc5769_2_1_request.hex")
	if len(wire) != 20+0x58 {
		t.Fatalf("request length = %d, want %d", len(wire), 20+0x58)
	}
	m, err := stun.Decode(wire, rfcKey)
	if err != nil {
		t.Fatalf("Decode: %v (kind=%s)", err, stunerror.Of(err))
	}
	if m.Type != stun.BindingRequest {
		t.Fatalf("type = 0x%04x, want BindingRequest", uint16(m.Type))
	}
	if !m.IntegrityOK {
		t.Fatal("MESSAGE-INTEGRITY of RFC 5769 §2.1 must verify")
	}
	if u, ok := m.Get(stun.AttrUsername); !ok || string(u.Value) != "evtj:h6vY" {
		t.Fatalf("USERNAME = %q ok=%v", string(u.Value), ok)
	}
	if sw, ok := m.Get(stun.AttrSoftware); !ok || string(sw.Value) != "STUN test client" {
		t.Fatalf("SOFTWARE = %q ok=%v", string(sw.Value), ok)
	}
	if pr, ok := m.Get(stun.AttrPriority); !ok || !bytes.Equal(pr.Value, []byte{0x6e, 0x00, 0x01, 0xff}) {
		t.Fatalf("PRIORITY value = %x ok=%v", pr.Value, ok)
	}
	if _, ok := m.Get(stun.AttrIceControlled); !ok {
		t.Fatal("ICE-CONTROLLED must be recognized")
	}
	if _, ok := m.Get(stun.AttrFingerprint); !ok {
		t.Fatal("FINGERPRINT attribute must be present")
	}
}

func TestRFC5769_2_2_ResponseIPv4KnownAnswer(t *testing.T) {
	wire := loadHexFixture(t, "rfc5769_2_2_response_ipv4.hex")
	m, err := stun.Decode(wire, rfcKey)
	if err != nil {
		t.Fatalf("Decode: %v (kind=%s)", err, stunerror.Of(err))
	}
	if !m.IntegrityOK {
		t.Fatal("integrity must verify")
	}
	ip, port, ok, err := m.XORMappedAddress()
	if err != nil || !ok {
		t.Fatalf("XORMappedAddress ok=%v err=%v", ok, err)
	}
	if ip.String() != "192.0.2.1" || port != 32853 {
		t.Fatalf("xor mapped = %s:%d, want 192.0.2.1:32853", ip, port)
	}
}

func TestRFC5769_2_3_ResponseIPv6KnownAnswer(t *testing.T) {
	wire := loadHexFixture(t, "rfc5769_2_3_response_ipv6.hex")
	m, err := stun.Decode(wire, rfcKey)
	if err != nil {
		t.Fatalf("Decode: %v (kind=%s)", err, stunerror.Of(err))
	}
	if !m.IntegrityOK {
		t.Fatal("integrity must verify")
	}
	ip, port, ok, err := m.XORMappedAddress()
	if err != nil || !ok {
		t.Fatalf("XORMappedAddress ok=%v err=%v", ok, err)
	}
	if ip.String() != "2001:db8:1234:5678:11:2233:4455:6677" || port != 32853 {
		t.Fatalf("xor mapped = %s:%d", ip, port)
	}
}

func TestXORAddressRFCWireBytes(t *testing.T) {
	txID := stun.TransactionID{}
	raw, _ := hex.DecodeString("b7e7a701bc34d686fa87dfae")
	copy(txID[:], raw)

	// IPv4 wire bytes from RFC 5769 §2.2.
	v4, err := stun.MarshalXORAddressValue(netIP("192.0.2.1"), 32853, txID)
	if err != nil {
		t.Fatal(err)
	}
	wantV4, _ := hex.DecodeString("0001a147e112a643")
	if !bytes.Equal(v4, wantV4) {
		t.Fatalf("IPv4 XOR bytes = %x, want %x", v4, wantV4)
	}

	// IPv6 wire bytes from RFC 5769 §2.3.
	v6, err := stun.MarshalXORAddressValue(netIP("2001:db8:1234:5678:11:2233:4455:6677"), 32853, txID)
	if err != nil {
		t.Fatal(err)
	}
	wantV6, _ := hex.DecodeString("0002a1470113a9faa5d3f179bc25f4b5bed2b9d9")
	if !bytes.Equal(v6, wantV6) {
		t.Fatalf("IPv6 XOR bytes = %x, want %x", v6, wantV6)
	}

	// Round-trip with a different txid proves the IPv6 mask really depends on
	// the transaction id: decoding under a wrong txid must yield a wrong IP.
	ip, port, derr := stun.UnmarshalXORAddressValue(v6, txID)
	if derr != nil || port != 32853 {
		t.Fatalf("decode v6: %v port=%d", derr, port)
	}
	if ip.String() != "2001:db8:1234:5678:11:2233:4455:6677" {
		t.Fatalf("v6 ip = %s", ip)
	}
	wrong := txID
	wrong[0] ^= 0xFF
	badIP, _, derr := stun.UnmarshalXORAddressValue(v6, wrong)
	if derr != nil {
		t.Fatal(err)
	}
	if badIP.Equal(ip) {
		t.Fatal("IPv6 decoded under a tampered transaction id unexpectedly matched")
	}
}

func TestPaddingIsFourByteAlignedAndIgnored(t *testing.T) {
	// Attribute value lengths 1..9: declared total length must match actual
	// wire length and the decoder must recover every value regardless of
	// padding byte content.
	for n := 1; n <= 9; n++ {
		tx := stun.MustTransactionID()
		m := stun.NewMessage(stun.BindingRequest, tx)
		m.Add(stun.AttrSoftware, bytes.Repeat([]byte{'A'}, n))
		wire, err := stun.Marshal(m, nil, false)
		if err != nil {
			t.Fatalf("n=%d marshal: %v", n, err)
		}
		// body: 4 header + n value + pad
		pad := (4 - n%4) % 4
		if want := stun.HeaderLen + 4 + n + pad; len(wire) != want {
			t.Fatalf("n=%d wire len=%d want=%d", n, len(wire), want)
		}
		dec, err := stun.Decode(wire, nil)
		if err != nil {
			t.Fatalf("n=%d decode: %v", n, err)
		}
		got, ok := dec.Get(stun.AttrSoftware)
		if !ok || len(got.Value) != n {
			t.Fatalf("n=%d recovered len=%d ok=%v", n, len(got.Value), ok)
		}
	}

	// Abnormal padding: padding bytes are nonzero. The decoder must ignore
	// their content while still accounting for them in the length.
	tx := stun.MustTransactionID()
	padAnomaly := rawAttrN(stun.AttrSoftware,
		append(bytes.Repeat([]byte{'B'}, 5), 0xEE, 0xEE, 0xEE), 5)
	wire := buildRaw(t, stun.BindingRequest, tx, nil, padAnomaly)
	dec, err := stun.Decode(wire, nil)
	if err != nil {
		t.Fatalf("nonzero padding must be tolerated: %v", err)
	}
	got, ok := dec.Get(stun.AttrSoftware)
	if !ok || string(got.Value) != "BBBBB" {
		t.Fatalf("value recovered as %q (len=%d)", string(got.Value), len(got.Value))
	}
}

func TestUnknownComprehensionRequiredRejected(t *testing.T) {
	tx := stun.MustTransactionID()

	// Required-range unknown attribute 0x0BAD.
	wire := buildRaw(t, stun.BindingRequest, tx, nil,
		rawAttr(stun.AttrType(0x0BAD), []byte{0x01, 0x02}))
	_, err := stun.Decode(wire, nil)
	if err == nil {
		t.Fatal("unknown required attribute must fail")
	}
	if stunerror.Of(err) != stunerror.KindIntegrity {
		t.Fatalf("kind = %s, want integrity", stunerror.Of(err))
	}
	var ure *stun.UnknownRequiredError
	if !asError(err, &ure) || len(ure.AttrTypes()) != 1 || ure.AttrTypes()[0] != 0x0BAD {
		t.Fatalf("error must carry the unknown type: %#v", err)
	}

	// Optional-range unknown attribute 0x80AD must be accepted and preserved.
	wireOK := buildRaw(t, stun.BindingRequest, tx, nil,
		rawAttr(stun.AttrType(0x80AD), []byte{1, 2, 3}))
	m, err := stun.Decode(wireOK, nil)
	if err != nil {
		t.Fatalf("unknown optional attribute must be accepted: %v", err)
	}
	if a, ok := m.Get(stun.AttrType(0x80AD)); !ok || len(a.Value) != 3 {
		t.Fatal("optional attribute not preserved")
	}
}

func TestDecodeErrorCategories(t *testing.T) {
	tx := stun.MustTransactionID()
	good := buildRaw(t, stun.BindingRequest, tx, nil,
		rawAttr(stun.AttrSoftware, []byte("ok")))

	cases := []struct {
		name     string
		mutate   func([]byte) []byte
		wantKind stunerror.Kind
		wrap     error
	}{
		{"short", func(b []byte) []byte { return b[:10] }, stunerror.KindInput, stun.ErrShortMessage},
		{"leading bits", func(b []byte) []byte { b[0] |= 0xC0; return b }, stunerror.KindInput, stun.ErrLeadingBits},
		{"bad cookie", func(b []byte) []byte { b[7] ^= 0xFF; return b }, stunerror.KindInput, stun.ErrBadCookie},
		{"length mismatch", func(b []byte) []byte { b[3]++; return b }, stunerror.KindInput, stun.ErrBadLength},
		{"truncated attr", func(b []byte) []byte {
			// Keep the message length consistent but claim the SOFTWARE
			// value is 12 bytes when only 3 follow.
			b[23] = 12
			return b
		}, stunerror.KindInput, stun.ErrTruncatedAttr},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := append([]byte(nil), good...)
			_, err := stun.Decode(tc.mutate(b), nil)
			if err == nil {
				t.Fatal("expected error")
			}
			if stunerror.Of(err) != tc.wantKind {
				t.Fatalf("kind = %s, want %s", stunerror.Of(err), tc.wantKind)
			}
			if !errorIs(err, tc.wrap) {
				t.Fatalf("error %v does not wrap %v", err, tc.wrap)
			}
		})
	}
}

func TestMessageIntegrityTamperCategories(t *testing.T) {
	tx := stun.MustTransactionID()
	m := stun.NewMessage(stun.BindingResponse, tx)
	if err := m.AddXORMappedAddress(netIP("198.51.100.7"), 4096); err != nil {
		t.Fatal(err)
	}
	key := []byte("unit-key")
	wire, err := stun.Marshal(m, key, true)
	if err != nil {
		t.Fatal(err)
	}

	// Wrong key -> integrity mismatch.
	if _, err := stun.Decode(wire, []byte("other-key")); stunerror.Of(err) != stunerror.KindIntegrity {
		t.Fatalf("wrong key kind = %s", stunerror.Of(err))
	}

	// Flip a byte inside the XOR address value (header 20 + attr hdr 4 + 2).
	tampered := append([]byte(nil), wire...)
	tampered[26] ^= 0xFF
	if _, err := stun.Decode(tampered, key); stunerror.Of(err) != stunerror.KindIntegrity {
		t.Fatalf("tampered body kind = %s", stunerror.Of(err))
	}

	// Flip the last byte (FINGERPRINT CRC).
	fp := append([]byte(nil), wire...)
	fp[len(fp)-1] ^= 0x01
	if _, err := stun.Decode(fp, key); stunerror.Of(err) != stunerror.KindIntegrity {
		t.Fatalf("tampered fingerprint kind = %s", stunerror.Of(err))
	}

	// MI present but verifier supplies no key -> compute-class configuration error.
	if _, err := stun.Decode(wire, nil); stunerror.Of(err) != stunerror.KindCompute {
		t.Fatalf("no-key kind = %s", stunerror.Of(err))
	}
}

func TestMarshalReproducesIntegritySemantics(t *testing.T) {
	// Two encoders must agree on the HMAC/FINGERPRINT of an independently
	// specified message. We rebuild RFC 5769 §2.2 semantics but with zero
	// padding (our encoder convention) and confirm Decode accepts; the exact
	// RFC vector with space padding is covered by the KAT test above.
	txID := stun.TransactionID{}
	copy(txID[:], mustHex(t, "b7e7a701bc34d686fa87dfae"))
	m := stun.NewMessage(stun.BindingResponse, txID)
	m.Add(stun.AttrSoftware, []byte("oracle-check-1")) // 14 bytes -> 2 pad
	if err := m.AddXORMappedAddress(netIP("192.0.2.1"), 32853); err != nil {
		t.Fatal(err)
	}
	wire, err := stun.Marshal(m, rfcKey, true)
	if err != nil {
		t.Fatal(err)
	}
	dec, err := stun.Decode(wire, rfcKey)
	if err != nil || !dec.IntegrityOK {
		t.Fatalf("self-produced integrity rejected: %v", err)
	}
}

func TestErrorCodeAttribute(t *testing.T) {
	tx := stun.MustTransactionID()
	m := stun.NewMessage(stun.BindingError, tx)
	m.AddErrorCode(stun.StatusUnknownAttribute, "Unknown Attribute")
	wire, err := stun.Marshal(m, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	dec, err := stun.Decode(wire, nil)
	if err != nil {
		t.Fatal(err)
	}
	code, reason, ok := dec.ErrorCode()
	if !ok || code != 420 || reason != "Unknown Attribute" {
		t.Fatalf("code=%d reason=%q ok=%v", code, reason, ok)
	}
}
