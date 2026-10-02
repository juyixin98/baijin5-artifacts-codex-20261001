package oracle_test

import (
	"encoding/hex"
	"math/rand"
	"testing"

	"berconf/internal/ber"
	"berconf/internal/oracle"
)

// TestMutationDifferential applies single-byte mutations to a set of
// valid encodings and compares verdicts. The two decoders may differ
// only in documented ways; specifically:
//
//   - the SUT must NEVER accept input that the mature library rejects
//     (the restricted profile is a strict subset);
//   - when the SUT rejects input the library accepts, the SUT category
//     must be one of the profile/DER/EOC categories the library is not
//     expected to enforce — never a silent success.
func TestMutationDifferential(t *testing.T) {
	seeds := []string{
		"3006020101020102",       // SEQUENCE{1,2}
		"a080308002010500000000", // nested indefinite
		"3008020101030300a1b2",   // SEQUENCE{INTEGER, BIT STRING}
		"a203020107",             // context [2]
		"02020080",               // INTEGER 128
		"308002010a0201140000",   // indefinite SEQUENCE{10,20}
	}

	profileAllowed := map[ber.ErrorKind]bool{
		ber.KindUnsupported:      true, // application/private tags
		ber.KindInvalidBitString: true, // content rule beyond structural BER
		ber.KindInvalidInteger:   true, // empty integer
		ber.KindMalformedEOC:     true, // stricter EOC semantics
		ber.KindTrailingData:     true, // single-value envelope
		ber.KindDepthExceeded:    true, // lower default depth than library
		ber.KindSizeExceeded:     true, // resource profile
		ber.KindLengthOverflow:   true, // length-of-length bound
		ber.KindInvalidEncoding:  true, // primitive/constructed & profile form rules the library does not enforce
		ber.KindInvalidTag:       true, // universal tag 0 reserved semantics
		ber.KindInvalidLength:    true, // e.g. reserved 0xFF framing the library may tolerate as content in some positions
	}

	rng := rand.New(rand.NewSource(20260927))
	total, sutOnly, bothReject, bothAccept := 0, 0, 0, 0
	for _, seedHex := range seeds {
		base, _ := hex.DecodeString(seedHex)
		for iter := 0; iter < 200; iter++ {
			mut := mutate(rng, base)
			total++

			ores := oracle.Decode(mut)
			_, serr := ber.DecodeAndValidate(mut, "BER", testMutationLimits())

			sutOK := serr == nil
			oracleOK := ores.OK

			switch {
			case !sutOK && !oracleOK:
				bothReject++
			case sutOK && oracleOK:
				bothAccept++
			case sutOK && !oracleOK:
				t.Fatalf("SUT accepted bytes the mature library rejected:\ninput=%x\nsut=ok\noracle=%v",
					mut, ores.ParseError)
			case !sutOK && oracleOK:
				de := asDecodeErr(t, serr)
				if !profileAllowed[de.Kind] {
					t.Fatalf("SUT rejected with non-profile category %s on library-accepted bytes\ninput=%x\nmsg=%s",
						de.Kind, mut, de.Msg)
				}
				sutOnly++
			}
		}
	}
	t.Logf("mutations=%d both_accept=%d both_reject=%d sut_stricter=%d",
		total, bothAccept, bothReject, sutOnly)
	if bothReject+sutOnly == 0 {
		t.Fatal("mutation run produced no rejections; test is ineffective")
	}
}

// TestTruncationSeries checks every prefix of valid inputs: any prefix
// shorter than the full value that the SUT accepts must also be accepted
// by the library with zero remainder; otherwise it must be TRUNCATED.
func TestTruncationSeries(t *testing.T) {
	seeds := []string{
		"3006020101020102",
		"a080308002010500000000",
		"02020080",
	}
	for _, seedHex := range seeds {
		base, _ := hex.DecodeString(seedHex)
		for n := 1; n < len(base); n++ {
			prefix := base[:n]
			ores := oracle.Decode(prefix)
			_, serr := ber.Decode(prefix, testMutationLimits())
			if serr == nil {
				// SUT accepted a proper prefix only if the prefix already
				// contains a complete value — library must agree exactly.
				if !ores.OK || ores.Rest != 0 {
					t.Fatalf("prefix %x accepted by SUT but oracle ok=%v rest=%d",
						prefix, ores.OK, ores.Rest)
				}
				continue
			}
			de := asDecodeErr(t, serr)
			if de.Kind != ber.KindTruncated && de.Kind != ber.KindMalformedEOC {
				t.Fatalf("prefix %x: kind=%s, want TRUNCATED/MALFORMED_EOC", prefix, de.Kind)
			}
			if de.Offset != int64(n) && de.Kind == ber.KindTruncated {
				// truncation offset must point at the first missing byte
				// unless an inner bound was hit first
				if de.Offset > int64(n) {
					t.Fatalf("prefix %x: truncation offset %d beyond end %d",
						prefix, de.Offset, n)
				}
			}
		}
	}
}

func testMutationLimits() ber.Limits {
	l := ber.DefaultLimits()
	l.MaxDepth = 1000 // match library for fair comparison
	return l
}

// mutate produces a single-byte mutation (flip, insert, delete or
// truncate) of a copy of base.
func mutate(rng *rand.Rand, base []byte) []byte {
	if len(base) == 0 {
		return []byte{0}
	}
	switch rng.Intn(4) {
	case 0: // flip one byte
		out := append([]byte(nil), base...)
		out[rng.Intn(len(out))] = byte(rng.Intn(256))
		return out
	case 1: // truncate
		n := rng.Intn(len(base)) + 1
		return append([]byte(nil), base[:n]...)
	case 2: // insert a random byte
		out := make([]byte, 0, len(base)+1)
		at := rng.Intn(len(base) + 1)
		out = append(out, base[:at]...)
		out = append(out, byte(rng.Intn(256)))
		out = append(out, base[at:]...)
		return out
	default: // delete one byte
		at := rng.Intn(len(base))
		out := make([]byte, 0, len(base)-1)
		out = append(out, base[:at]...)
		out = append(out, base[at+1:]...)
		return out
	}
}
