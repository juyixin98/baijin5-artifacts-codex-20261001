package compat

import (
	"encoding/hex"
	"testing"

	"hpacklab/state"
)

// runVectors feeds each block of a sequence into one decoder (the RFC
// examples are stateful across blocks) and checks fields, dynamic table
// contents and table size against the values published in RFC 7541.
func runVectors(t *testing.T, maxTableSize int, vecs []vector) {
	t.Helper()
	dec := state.NewDecoder(maxTableSize, state.DefaultLimits)
	for _, v := range vecs {
		block, err := hex.DecodeString(v.hex)
		if err != nil {
			t.Fatalf("%s: bad hex in test fixture: %v", v.name, err)
		}
		fields, _, err := dec.Decode(block)
		if err != nil {
			t.Fatalf("%s: decode: %v", v.name, err)
		}
		if len(fields) != len(v.want) {
			t.Fatalf("%s: got %d fields %v, want %d", v.name, len(fields), fields, len(v.want))
		}
		for i, w := range v.want {
			if fields[i].Name != w[0] || fields[i].Value != w[1] {
				t.Fatalf("%s: field %d = %q=%q, want %q=%q",
					v.name, i, fields[i].Name, fields[i].Value, w[0], w[1])
			}
		}
		if v.wantDynEntries != nil {
			got := dec.Table().Dyn.Entries()
			if len(got) != len(v.wantDynEntries) {
				t.Fatalf("%s: dyn entries = %v, want %v", v.name, got, v.wantDynEntries)
			}
			for i, w := range v.wantDynEntries {
				if got[i].Name != w[0] || got[i].Value != w[1] {
					t.Fatalf("%s: dyn entry %d = %q=%q, want %q=%q",
						v.name, i, got[i].Name, got[i].Value, w[0], w[1])
				}
			}
		}
		if v.wantDynSize >= 0 {
			if got := dec.Table().Dyn.Size(); got != v.wantDynSize {
				t.Fatalf("%s: dyn size = %d, want %d", v.name, got, v.wantDynSize)
			}
		}
	}
}

func TestRFC7541_C3_RequestsWithoutHuffman(t *testing.T)  { runVectors(t, 4096, c3) }
func TestRFC7541_C4_RequestsWithHuffman(t *testing.T)     { runVectors(t, 4096, c4) }
func TestRFC7541_C5_ResponsesWithoutHuffman(t *testing.T) { runVectors(t, 256, c5) }
func TestRFC7541_C6_ResponsesWithHuffman(t *testing.T)    { runVectors(t, 256, c6) }
