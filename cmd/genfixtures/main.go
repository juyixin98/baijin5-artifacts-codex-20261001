// Command genfixtures regenerates the local synthetic test fixtures.
//
// Expected answers come from TWO independent sources, never from the
// codec under test:
//  1. hand-authored byte vectors with values derived on paper (marked
//     "source": "hand");
//  2. vectors produced by the mature library go-asn1-ber (marked
//     "source": "go-asn1-ber").
//
// Output files are written to fixtures/ and checked into the tree so
// tests run fully offline once generated.
package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"math/big"
	"os"
	"path/filepath"

	berlib "github.com/go-asn1-ber/asn1-ber"
)

type Fixture struct {
	ID           string         `json:"id"`
	Description  string         `json:"description"`
	Source       string         `json:"source"` // hand | go-asn1-ber
	InputHex     string         `json:"input_hex"`
	ExpectOK     bool           `json:"expect_ok"`
	ExpectKind   string         `json:"expect_kind,omitempty"`
	ExpectOffset int64          `json:"expect_offset,omitempty"`
	ExpectMode   string         `json:"expect_mode,omitempty"` // BER | DER
	Notes        []string       `json:"notes,omitempty"`
	ExpectValue  *expectedValue `json:"expect_value,omitempty"`
}

type expectedValue struct {
	Class       string          `json:"class"`
	Tag         uint32          `json:"tag"`
	Constructed bool            `json:"constructed"`
	IntegerDec  string          `json:"integer_decimal,omitempty"`
	ValueHex    string          `json:"value_hex,omitempty"`
	Children    []expectedValue `json:"children,omitempty"`
}

func hx(b []byte) string { return hex.EncodeToString(b) }

func main() {
	outDir := flag.String("out", "fixtures", "output directory")
	flag.Parse()
	if err := run(*outDir); err != nil {
		fmt.Fprintf(os.Stderr, "genfixtures: %v\n", err)
		os.Exit(1)
	}
}

func run(outDir string) error {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}
	var fixtures []Fixture
	fixtures = append(fixtures, handFixtures()...)
	fixtures = append(fixtures, oracleFixtures()...)

	data, err := json.MarshalIndent(fixtures, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if err := os.WriteFile(filepath.Join(outDir, "vectors.json"), data, 0o644); err != nil {
		return err
	}
	fmt.Printf("wrote %d fixtures to %s/vectors.json\n", len(fixtures), outDir)
	return nil
}

// handFixtures are derived on paper.
func handFixtures() []Fixture {
	var f []Fixture

	// --- INTEGER ---
	f = append(f, Fixture{
		ID: "int-zero", Description: "INTEGER 0 minimal", Source: "hand",
		InputHex: "020100", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 2, IntegerDec: "0", ValueHex: "00"},
	})
	f = append(f, Fixture{
		ID: "int-127", Description: "INTEGER 127", Source: "hand",
		InputHex: "02017f", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 2, IntegerDec: "127", ValueHex: "7f"},
	})
	f = append(f, Fixture{
		ID: "int-128", Description: "INTEGER 128 needs leading 0x00", Source: "hand",
		InputHex: "02020080", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 2, IntegerDec: "128", ValueHex: "0080"},
	})
	f = append(f, Fixture{
		ID: "int-neg1", Description: "INTEGER -1 -> FF", Source: "hand",
		InputHex: "0201ff", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 2, IntegerDec: "-1", ValueHex: "ff"},
	})
	f = append(f, Fixture{
		ID: "int-neg128", Description: "INTEGER -128 -> 80", Source: "hand",
		InputHex: "020180", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 2, IntegerDec: "-128", ValueHex: "80"},
	})
	f = append(f, Fixture{
		ID: "int-nonminimal-der", Description: "non-minimal INTEGER (02 02 0000): valid BER, invalid DER", Source: "hand",
		InputHex: "02020000", ExpectOK: false, ExpectMode: "DER",
		ExpectKind: "INVALID_ENCODING", ExpectOffset: 2,
		Notes: []string{"offset 2 = first content octet (header end), where redundancy starts"},
	})
	f = append(f, Fixture{
		ID: "int-empty", Description: "empty INTEGER content", Source: "hand",
		InputHex: "0200", ExpectOK: false,
		ExpectKind: "INVALID_INTEGER", ExpectOffset: 2,
	})

	// --- BIT STRING ---
	f = append(f, Fixture{
		ID: "bits-ok", Description: "BIT STRING 0 unused bits, A1", Source: "hand",
		InputHex: "030200a1", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 3, ValueHex: "00a1"},
	})
	f = append(f, Fixture{
		ID: "bits-unused-ok", Description: "BIT STRING 3 unused bits, low bits zero (80)", Source: "hand",
		InputHex: "03020380", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 3, ValueHex: "0380"},
	})
	f = append(f, Fixture{
		ID: "bits-trailing-one", Description: "BIT STRING 3 unused bits but a low bit set (81)", Source: "hand",
		InputHex: "03020381", ExpectOK: false,
		ExpectKind: "INVALID_BIT_STRING", ExpectOffset: 3,
		Notes: []string{"offset 3 = offending final content octet"},
	})
	f = append(f, Fixture{
		ID: "bits-unused-8", Description: "BIT STRING unused-bits octet = 8", Source: "hand",
		InputHex: "03020800", ExpectOK: false,
		ExpectKind: "INVALID_BIT_STRING", ExpectOffset: 2,
	})
	f = append(f, Fixture{
		ID: "bits-empty-nonzero-unused", Description: "empty BIT STRING with unused bits = 1", Source: "hand",
		InputHex: "030101", ExpectOK: false,
		ExpectKind: "INVALID_BIT_STRING", ExpectOffset: 2,
	})
	f = append(f, Fixture{
		ID: "bits-missing-unused", Description: "BIT STRING without unused-bits octet", Source: "hand",
		InputHex: "0300", ExpectOK: false,
		ExpectKind: "INVALID_BIT_STRING", ExpectOffset: 2,
	})

	// --- SEQUENCE ---
	f = append(f, Fixture{
		ID: "seq-two-ints", Description: "SEQUENCE { 1, 2 }", Source: "hand",
		InputHex: "3006020101020102", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 16, Constructed: true, Children: []expectedValue{
			{Class: "universal", Tag: 2, IntegerDec: "1", ValueHex: "01"},
			{Class: "universal", Tag: 2, IntegerDec: "2", ValueHex: "02"},
		}},
	})

	// --- indefinite length & EOC behaviour ---
	f = append(f, Fixture{
		ID: "nested-indefinite", Description: "nested indefinite [0]{ SEQUENCE { INTEGER 5 } }", Source: "hand",
		InputHex: "a080308002010500000000", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "context", Tag: 0, Constructed: true, Children: []expectedValue{
			{Class: "universal", Tag: 16, Constructed: true, Children: []expectedValue{
				{Class: "universal", Tag: 2, IntegerDec: "5", ValueHex: "05"},
			}},
		}},
	})
	f = append(f, Fixture{
		ID: "primitive-indefinite", Description: "indefinite length on primitive INTEGER", Source: "hand",
		InputHex: "02800100", ExpectOK: false,
		ExpectKind: "INVALID_LENGTH", ExpectOffset: 1,
	})
	f = append(f, Fixture{
		ID: "bad-eoc-in-definite", Description: "EOC inside a definite-length SEQUENCE terminates nothing", Source: "hand",
		InputHex: "300400000200", ExpectOK: false,
		ExpectKind: "MALFORMED_EOC", ExpectOffset: 2,
		Notes: []string{"EOC must not be swallowed as a zero-length value"},
	})
	f = append(f, Fixture{
		ID: "toplevel-eoc", Description: "EOC as the top-level value", Source: "hand",
		InputHex: "0000", ExpectOK: false,
		ExpectKind: "MALFORMED_EOC", ExpectOffset: 0,
	})
	f = append(f, Fixture{
		ID: "extra-eoc", Description: "extra EOC after the construction closed", Source: "hand",
		InputHex: "a08002010100000000", ExpectOK: false,
		ExpectKind: "MALFORMED_EOC", ExpectOffset: 7,
		Notes: []string{"a bare 00 00 after a closed value is an EOC with no construction, not trailing data"},
	})
	f = append(f, Fixture{
		ID: "missing-eoc", Description: "indefinite value with no terminator", Source: "hand",
		InputHex: "a080020101", ExpectOK: false,
		ExpectKind: "TRUNCATED", ExpectOffset: 5,
	})

	// --- truncation / framing ---
	f = append(f, Fixture{
		ID: "truncated-content", Description: "definite length exceeds available bytes", Source: "hand",
		InputHex: "020301", ExpectOK: false,
		ExpectKind: "TRUNCATED", ExpectOffset: 3,
	})
	f = append(f, Fixture{
		ID: "truncated-tag", Description: "only identifier octet present", Source: "hand",
		InputHex: "02", ExpectOK: false,
		ExpectKind: "TRUNCATED", ExpectOffset: 1,
	})
	f = append(f, Fixture{
		ID: "truncated-long-length", Description: "long-form length declares 2 octets, none follow", Source: "hand",
		InputHex: "0282", ExpectOK: false,
		ExpectKind: "TRUNCATED", ExpectOffset: 2,
	})
	f = append(f, Fixture{
		ID: "trailing-bytes", Description: "extra bytes after a complete top-level value", Source: "hand",
		InputHex: "02010142", ExpectOK: false,
		ExpectKind: "TRAILING_DATA", ExpectOffset: 3,
	})
	f = append(f, Fixture{
		ID: "reserved-length", Description: "length octet 0xFF reserved", Source: "hand",
		InputHex: "02ff00", ExpectOK: false,
		ExpectKind: "INVALID_LENGTH", ExpectOffset: 1,
	})

	// --- DER-only canonical constraints ---
	f = append(f, Fixture{
		ID: "der-nonminimal-length", Description: "long-form length for a 1-byte content", Source: "hand",
		InputHex: "02810105", ExpectOK: false, ExpectMode: "DER",
		ExpectKind: "INVALID_ENCODING", ExpectOffset: 1,
	})
	f = append(f, Fixture{
		ID: "der-indefinite", Description: "indefinite SEQUENCE is valid BER but not DER", Source: "hand",
		InputHex: "30800000", ExpectOK: false, ExpectMode: "DER",
		ExpectKind: "INVALID_ENCODING", ExpectOffset: 1,
	})

	// --- context tags ---
	f = append(f, Fixture{
		ID: "context2-int", Description: "constructed context tag [2] containing INTEGER 7", Source: "hand",
		InputHex: "a203020107", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "context", Tag: 2, Constructed: true, Children: []expectedValue{
			{Class: "universal", Tag: 2, IntegerDec: "7", ValueHex: "07"},
		}},
	})
	f = append(f, Fixture{
		ID: "context5-primitive", Description: "primitive context tag [5] opaque value", Source: "hand",
		InputHex: "8502cafe", ExpectOK: true,
		ExpectValue: &expectedValue{Class: "context", Tag: 5, ValueHex: "cafe"},
	})
	return f
}

// oracleFixtures are produced by go-asn1-ber (independent mature library).
func oracleFixtures() []Fixture {
	var f []Fixture

	intCases := []struct {
		id, desc string
		v        *big.Int
	}{
		{"oracle-int-256", "INTEGER 256", big.NewInt(256)},
		{"oracle-int-neg256", "INTEGER -256", big.NewInt(-256)},
		{"oracle-int-huge", "301-byte positive integer 2^2400-7", new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 2400), big.NewInt(7))},
		{"oracle-int-huge-neg", "301-byte negative integer -(2^2400-7)", new(big.Int).Neg(new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 2400), big.NewInt(7)))},
	}
	for _, tc := range intCases {
		// big.Int is not supported by the library's typed constructor, so
		// the packet is built through its generic encoder with raw
		// two's-complement content.
		twos := twosComplement(tc.v)
		p := berlib.Encode(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, nil, "")
		p.Data.Write(twos)
		f = append(f, Fixture{
			ID: tc.id, Description: tc.desc, Source: "go-asn1-ber",
			InputHex: hx(p.Bytes()), ExpectOK: true,
			ExpectValue: &expectedValue{Class: "universal", Tag: 2, IntegerDec: tc.v.String()},
		})
	}

	// SEQUENCE { INTEGER 1, [0]{ INTEGER 2 } } via oracle
	seq := berlib.NewSequence("")
	seq.AppendChild(berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, 1, ""))
	wrapped := berlib.Encode(berlib.ClassContext, berlib.TypeConstructed, berlib.Tag(0), nil, "")
	wrapped.AppendChild(berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, 2, ""))
	seq.AppendChild(wrapped)
	f = append(f, Fixture{
		ID: "oracle-seq-context", Description: "SEQUENCE { INTEGER 1, [0]{ INTEGER 2 } }", Source: "go-asn1-ber",
		InputHex: hx(seq.Bytes()), ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 16, Constructed: true, Children: []expectedValue{
			{Class: "universal", Tag: 2, IntegerDec: "1"},
			{Class: "context", Tag: 0, Constructed: true, Children: []expectedValue{
				{Class: "universal", Tag: 2, IntegerDec: "2"},
			}},
		}},
	})

	// Indefinite SEQUENCE { 10, 20 }: v1.5.8 has no indefinite encoder, so
	// the library produces the child TLVs and the X.690 indefinite framing
	// (30 80 .. 00 00) is applied around them; semantics stay library-derived.
	c1 := berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, 10, "").Bytes()
	c2 := berlib.NewInteger(berlib.ClassUniversal, berlib.TypePrimitive, berlib.TagInteger, 20, "").Bytes()
	indef := append([]byte{0x30, 0x80}, append(c1, c2...)...)
	indef = append(indef, 0x00, 0x00)
	f = append(f, Fixture{
		ID: "oracle-indef-seq", Description: "indefinite SEQUENCE { 10, 20 } (child TLVs from oracle)", Source: "go-asn1-ber",
		InputHex: hx(indef), ExpectOK: true,
		ExpectValue: &expectedValue{Class: "universal", Tag: 16, Constructed: true, Children: []expectedValue{
			{Class: "universal", Tag: 2, IntegerDec: "10"},
			{Class: "universal", Tag: 2, IntegerDec: "20"},
		}},
	})
	return f
}

// twosComplement renders v as minimal two's-complement big-endian bytes.
func twosComplement(v *big.Int) []byte {
	if v.Sign() == 0 {
		return []byte{0}
	}
	if v.Sign() > 0 {
		b := v.Bytes()
		if b[0]&0x80 != 0 {
			return append([]byte{0x00}, b...)
		}
		return b
	}
	mag := new(big.Int).Neg(v)
	w := 1
	half := new(big.Int).Lsh(big.NewInt(1), 7)
	for mag.Cmp(half) > 0 {
		w++
		half.Lsh(half, 8)
	}
	mod := new(big.Int).Lsh(big.NewInt(1), uint(8*w))
	tc := new(big.Int).Add(v, mod)
	out := tc.Bytes()
	if len(out) < w {
		out = append(make([]byte, w-len(out)), out...)
	}
	return out
}
