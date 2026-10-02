package stun_test

// Golden-vector tests driven by test/testdata/fixtures.json, which is produced
// by the INDEPENDENT Python oracle (test/oracle/stun_oracle.py). The Go code
// never generates these expected bytes; it decodes the oracle's bytes and
// re-encodes its own values, requiring byte-identity with the oracle.

import (
	"encoding/binary"
	"encoding/hex"
	"os"
	"testing"

	"stunlab/internal/stun"
)

type fixtures struct {
	RFC []struct {
		Name               string `json:"name"`
		IP                 string `json:"ip"`
		Port               int    `json:"port"`
		ValueHex           string `json:"value_hex"`
		OracleEncodesRight bool   `json:"oracle_encodes_correctly"`
	} `json:"rfc5769_xor_vectors"`
	Messages []struct {
		Name      string `json:"name"`
		TxnHex    string `json:"txn_hex"`
		IP        string `json:"ip"`
		Port      int    `json:"port"`
		Integrity bool   `json:"integrity"`
		Key       string `json:"key"`
		Hex       string `json:"hex"`
	} `json:"messages"`
	Negative []struct {
		Name       string `json:"name"`
		Hex        string `json:"hex"`
		Key        string `json:"key"`
		ExpectKind string `json:"expect_kind"`
	} `json:"integrity_negative"`
	Padding []struct {
		ValueLen  int `json:"value_len"`
		WireLen   int `json:"wire_len"`
		TotalWire int `json:"total_wire"`
	} `json:"padding_vectors"`
	Errors []struct {
		Name   string `json:"name"`
		Code   int    `json:"code"`
		Reason string `json:"reason"`
		Hex    string `json:"hex"`
	} `json:"error_messages"`
}

func loadFixtures(t *testing.T) fixtures {
	t.Helper()
	var fx fixtures
	raw, err := os.ReadFile("../../test/testdata/fixtures.json")
	if err != nil {
		t.Fatalf("load fixtures (run the python oracle first): %v", err)
	}
	if err := jsonUnmarshal(raw, &fx); err != nil {
		t.Fatal(err)
	}
	if len(fx.Messages) == 0 {
		t.Fatal("fixtures empty")
	}
	return fx
}

func TestGoldenRFC5769XORVectors(t *testing.T) {
	fx := loadFixtures(t)
	for _, v := range fx.RFC {
		t.Run(v.Name, func(t *testing.T) {
			if !v.OracleEncodesRight {
				t.Fatal("oracle failed its own RFC 5769 check; fixtures are untrustworthy")
			}
			want, _ := hex.DecodeString(v.ValueHex)
			var txn stun.TransactionID
			txnBytes, _ := hex.DecodeString("b7e7a701bc34d686fa87dfae")
			copy(txn[:], txnBytes)
			got, err := stun.EncodeXORMappedAddress(
				stun.Address{IP: parseIP(v.IP), Port: v.Port}, txn)
			if err != nil {
				t.Fatal(err)
			}
			if hex.EncodeToString(got) != v.ValueHex {
				t.Fatalf("Go XOR encode %x != oracle/RFC %s", got, v.ValueHex)
			}
			addr, err := stun.DecodeXORMappedAddress(want, txn)
			if err != nil {
				t.Fatal(err)
			}
			if !addr.IP.Equal(parseIP(v.IP)) || addr.Port != v.Port {
				t.Fatalf("Go decode = %s:%d, want %s:%d", addr.IP, addr.Port, v.IP, v.Port)
			}
		})
	}
}

func TestGoldenDecodeOracleMessages(t *testing.T) {
	fx := loadFixtures(t)
	for _, m := range fx.Messages {
		t.Run(m.Name, func(t *testing.T) {
			raw, _ := hex.DecodeString(m.Hex)
			msg, err := stun.UnmarshalMessage(raw)
			if err != nil {
				t.Fatalf("Go rejected oracle message: %v", err)
			}
			if msg.Class != stun.ClassSuccessResponse || msg.Method != stun.MethodBinding {
				t.Fatalf("type = %04x/%04x", uint16(msg.Method), uint16(msg.Class))
			}
			var txn stun.TransactionID
			tb, _ := hex.DecodeString(m.TxnHex)
			copy(txn[:], tb)
			if msg.TransactionID != txn {
				t.Fatal("txn mismatch")
			}
			v, ok := msg.Attribute(stun.AttrXORMappedAddress)
			if !ok {
				t.Fatal("oracle message missing XOR-MAPPED-ADDRESS")
			}
			addr, err := stun.DecodeXORMappedAddress(v, txn)
			if err != nil {
				t.Fatal(err)
			}
			if !addr.IP.Equal(parseIP(m.IP)) || addr.Port != m.Port {
				t.Fatalf("Go decoded %s:%d != oracle %s:%d", addr.IP, addr.Port, m.IP, m.Port)
			}
			// Go re-encoding of the attribute must equal the oracle bytes.
			re, _ := stun.EncodeXORMappedAddress(
				stun.Address{IP: parseIP(m.IP), Port: m.Port}, txn)
			if hex.EncodeToString(re) != hex.EncodeToString(v) {
				t.Fatalf("re-encode %x != oracle %x", re, v)
			}
			if m.Integrity {
				if err := stun.VerifyMessageIntegrity(raw, []byte(m.Key)); err != nil {
					t.Fatalf("Go rejected oracle HMAC: %v", err)
				}
			}
		})
	}
}

func TestGoldenGoMessagesVerifyInOracleCrossover(t *testing.T) {
	// The reverse direction: Go builds messages and must match oracle bytes
	// exactly for identical inputs (oracle fixtures encode the same fields).
	fx := loadFixtures(t)
	for _, m := range fx.Messages {
		t.Run(m.Name, func(t *testing.T) {
			var txn stun.TransactionID
			tb, _ := hex.DecodeString(m.TxnHex)
			copy(txn[:], tb)
			xorv, _ := stun.EncodeXORMappedAddress(
				stun.Address{IP: parseIP(m.IP), Port: m.Port}, txn)
			mapv, _ := stun.EncodeMappedAddress(
				stun.Address{IP: parseIP(m.IP), Port: m.Port})
			attrs := []stun.Attribute{
				{Type: stun.AttrXORMappedAddress, Value: xorv},
				{Type: stun.AttrMappedAddress, Value: mapv},
				{Type: stun.AttrSoftware, Value: []byte("stunlab-oracle/1.0")},
			}
			var got []byte
			var err error
			if m.Integrity {
				got, err = stun.AddMessageIntegrity(stun.MethodBinding,
					stun.ClassSuccessResponse, txn, attrs, []byte(m.Key))
			} else {
				got, err = stun.Marshal(stun.MethodBinding,
					stun.ClassSuccessResponse, txn, attrs)
			}
			if err != nil {
				t.Fatal(err)
			}
			if hex.EncodeToString(got) != m.Hex {
				t.Fatalf("Go message differs from oracle byte-for-byte\n go=%s\nor=%s",
					hex.EncodeToString(got), m.Hex)
			}
		})
	}
}

func TestGoldenNegativeIntegrityVectors(t *testing.T) {
	fx := loadFixtures(t)
	for _, n := range fx.Negative {
		t.Run(n.Name, func(t *testing.T) {
			raw, _ := hex.DecodeString(n.Hex)
			key := []byte(n.Key)
			if key == nil || n.Key == "" {
				key = []byte("lab-shared-secret")
			}
			// The oracle classifies declared-length mismatch as input_error;
			// Go's structural parser sees that first. Otherwise both must
			// report integrity_failure.
			var kind stun.ErrorKind
			if _, err := stun.UnmarshalMessage(raw); err != nil {
				kind = stun.ErrorOf(err)
			} else if err := stun.VerifyMessageIntegrity(raw, key); err != nil {
				kind = stun.ErrorOf(err)
			}
			if string(kind) != n.ExpectKind {
				t.Fatalf("Go kind=%q, oracle expects %q", kind, n.ExpectKind)
			}
		})
	}
}

func TestGoldenErrorMessages(t *testing.T) {
	fx := loadFixtures(t)
	for _, e := range fx.Errors {
		t.Run(e.Name, func(t *testing.T) {
			raw, _ := hex.DecodeString(e.Hex)
			msg, err := stun.UnmarshalMessage(raw)
			if err != nil {
				t.Fatal(err)
			}
			if msg.Class != stun.ClassErrorResponse {
				t.Fatalf("class = %04x", uint16(msg.Class))
			}
			v, ok := msg.Attribute(stun.AttrErrorCode)
			if !ok {
				t.Fatal("no ERROR-CODE")
			}
			ec, err := stun.DecodeErrorCode(v)
			if err != nil {
				t.Fatal(err)
			}
			if ec.Code != e.Code || ec.Reason != e.Reason {
				t.Fatalf("Go decoded %d %q != oracle %d %q", ec.Code, ec.Reason, e.Code, e.Reason)
			}
		})
	}
}

func TestGoldenPaddingWireLengths(t *testing.T) {
	fx := loadFixtures(t)
	for _, p := range fx.Padding {
		val := make([]byte, p.ValueLen)
		body, err := stun.EncodeAttributes([]stun.Attribute{
			{Type: stun.AttrSoftware, Value: val},
		})
		if err != nil {
			t.Fatal(err)
		}
		if len(body) != p.WireLen {
			t.Fatalf("value_len=%d Go wire=%d oracle=%d", p.ValueLen, len(body), p.WireLen)
		}
		// Header + body total consistency check vs oracle.
		if 20+len(body) != p.TotalWire {
			t.Fatalf("total mismatch")
		}
	}
}

func TestGoldenFixturesWellFormed(t *testing.T) {
	// Guard against an empty/stale fixture file being silently accepted.
	fx := loadFixtures(t)
	if len(fx.RFC) != 2 || len(fx.Negative) < 4 || len(fx.Errors) != 2 {
		t.Fatalf("unexpected fixture counts: %+v", fx)
	}
	for _, m := range fx.Messages {
		raw, _ := hex.DecodeString(m.Hex)
		if binary.BigEndian.Uint32(raw[4:8]) != stun.MagicCookie {
			t.Fatalf("%s bad cookie", m.Name)
		}
	}
}
