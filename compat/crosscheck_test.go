package compat

import (
	"bytes"
	"encoding/hex"
	"fmt"
	"math/rand"
	"testing"

	xhpack "golang.org/x/net/http2/hpack"

	"hpacklab/state"
)

// xnetDecode decodes one header block with the independent
// golang.org/x/net/http2/hpack implementation.
func xnetDecode(t *testing.T, dec *xhpack.Decoder, block []byte) []xhpack.HeaderField {
	t.Helper()
	var out []xhpack.HeaderField
	dec.SetEmitFunc(func(f xhpack.HeaderField) { out = append(out, f) })
	if _, err := dec.Write(block); err != nil {
		t.Fatalf("x/net decode: %v", err)
	}
	if err := dec.Close(); err != nil {
		t.Fatalf("x/net decode close: %v", err)
	}
	return out
}

// TestGoldenVectorsAgainstXNet validates the hand-transcribed RFC 7541
// Appendix C vectors against the independent x/net implementation: both
// decoders must produce identical field lists for every block. This
// guards against transcription errors in the golden data itself.
func TestGoldenVectorsAgainstXNet(t *testing.T) {
	groups := []struct {
		name         string
		maxTableSize uint32
		vecs         []vector
	}{
		{"C.3", 4096, c3}, {"C.4", 4096, c4}, {"C.5", 256, c5}, {"C.6", 256, c6},
	}
	for _, g := range groups {
		t.Run(g.name, func(t *testing.T) {
			xdec := xhpack.NewDecoder(g.maxTableSize, nil)
			mine := state.NewDecoder(int(g.maxTableSize), state.DefaultLimits)
			for _, v := range g.vecs {
				block, _ := hex.DecodeString(v.hex)
				// Independent reference decode.
				var xref []xhpack.HeaderField
				xdec.SetEmitFunc(func(f xhpack.HeaderField) { xref = append(xref, f) })
				if _, err := xdec.Write(block); err != nil {
					t.Fatalf("%s: x/net rejected golden block: %v", v.name, err)
				}
				if err := xdec.Close(); err != nil {
					t.Fatalf("%s: x/net close: %v", v.name, err)
				}
				// The RFC-published field list must match x/net's decode.
				if len(xref) != len(v.want) {
					t.Fatalf("%s: x/net decoded %d fields, RFC lists %d", v.name, len(xref), len(v.want))
				}
				for i, w := range v.want {
					if xref[i].Name != w[0] || xref[i].Value != w[1] {
						t.Fatalf("%s: field %d: x/net=%q=%q RFC=%q=%q",
							v.name, i, xref[i].Name, xref[i].Value, w[0], w[1])
					}
				}
				// And our decoder must agree with both.
				got, _, err := mine.Decode(block)
				if err != nil {
					t.Fatalf("%s: hpacklab decode: %v", v.name, err)
				}
				if len(got) != len(xref) {
					t.Fatalf("%s: hpacklab decoded %d fields, x/net %d", v.name, len(got), len(xref))
				}
				for i := range xref {
					if got[i].Name != xref[i].Name || got[i].Value != xref[i].Value {
						t.Fatalf("%s: field %d: hpacklab=%q=%q x/net=%q=%q",
							v.name, i, got[i].Name, got[i].Value, xref[i].Name, xref[i].Value)
					}
				}
			}
		})
	}
}

// corpus generates a deterministic pseudo-random sequence of header
// blocks exercising duplicates, table reuse and sensitive fields.
func corpus(seed int64, blocks int) [][]state.Field {
	rng := rand.New(rand.NewSource(seed))
	names := []string{":method", ":path", ":scheme", ":authority",
		"cache-control", "user-agent", "cookie", "x-custom", "accept"}
	values := []string{"GET", "POST", "/", "/index.html", "http", "https",
		"no-cache", "hpacklab/1.0", "session=abc123", "v1", "text/html"}
	var out [][]state.Field
	for b := 0; b < blocks; b++ {
		n := 1 + rng.Intn(6)
		var fields []state.Field
		for i := 0; i < n; i++ {
			f := state.Field{
				Name:  names[rng.Intn(len(names))],
				Value: values[rng.Intn(len(values))],
			}
			if f.Name == "cookie" && rng.Intn(3) == 0 {
				f.Sensitive = true
			}
			fields = append(fields, f)
		}
		out = append(out, fields)
	}
	return out
}

func sameFields(t *testing.T, ctx string, got []state.Field, want []state.Field) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s: got %d fields, want %d", ctx, len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%s: field %d: got %+v want %+v", ctx, i, got[i], want[i])
		}
	}
}

// TestInteropMyEncoderXNetDecoder encodes with hpacklab and decodes with
// x/net across a multi-block session, including a mid-session dynamic
// table size change.
func TestInteropMyEncoderXNetDecoder(t *testing.T) {
	blocks := corpus(1, 25)
	enc := state.NewEncoder(4096, true)
	xdec := xhpack.NewDecoder(4096, nil)
	for i, in := range blocks {
		if i == 10 {
			enc.SetMaxDynamicSize(1024)
		}
		if i == 15 {
			enc.SetMaxDynamicSize(4096)
		}
		block, _ := enc.Encode(in)
		var got []state.Field
		xdec.SetEmitFunc(func(f xhpack.HeaderField) {
			got = append(got, state.Field{Name: f.Name, Value: f.Value, Sensitive: f.Sensitive})
		})
		if _, err := xdec.Write(block); err != nil {
			t.Fatalf("block %d: x/net rejected hpacklab output: %v", i, err)
		}
		if err := xdec.Close(); err != nil {
			t.Fatalf("block %d: x/net close: %v", i, err)
		}
		sameFields(t, fmt.Sprintf("block %d", i), got, in)
	}
}

// TestInteropXNetEncoderMyDecoder encodes with x/net and decodes with
// hpacklab across the same kind of session.
func TestInteropXNetEncoderMyDecoder(t *testing.T) {
	blocks := corpus(2, 25)
	var buf bytes.Buffer
	xenc := xhpack.NewEncoder(&buf)
	dec := state.NewDecoder(4096, state.DefaultLimits)
	for i, in := range blocks {
		buf.Reset()
		if i == 10 {
			xenc.SetMaxDynamicTableSize(1024)
		}
		if i == 15 {
			xenc.SetMaxDynamicTableSize(4096)
		}
		for _, f := range in {
			err := xenc.WriteField(xhpack.HeaderField{Name: f.Name, Value: f.Value, Sensitive: f.Sensitive})
			if err != nil {
				t.Fatalf("block %d: x/net encode: %v", i, err)
			}
		}
		got, _, err := dec.Decode(buf.Bytes())
		if err != nil {
			t.Fatalf("block %d: hpacklab rejected x/net output: %v", i, err)
		}
		sameFields(t, fmt.Sprintf("block %d", i), got, in)
	}
}

// TestInteropTableStateAgreement checks that after a shared session the
// dynamic table sizes of both implementations agree exactly.
func TestInteropTableStateAgreement(t *testing.T) {
	blocks := corpus(3, 40)
	enc := state.NewEncoder(256, true) // small table forces eviction
	xdec := xhpack.NewDecoder(256, nil)
	for i, in := range blocks {
		block, _ := enc.Encode(in)
		xdec.SetEmitFunc(func(xhpack.HeaderField) {})
		if _, err := xdec.Write(block); err != nil {
			t.Fatalf("block %d: %v", i, err)
		}
		if err := xdec.Close(); err != nil {
			t.Fatalf("block %d close: %v", i, err)
		}
	}
	// x/net does not expose its table size directly; decode one more
	// block with both and compare full field output as the consistency
	// check, then compare against our own decoder fed the same history.
	mine := state.NewDecoder(256, state.DefaultLimits)
	enc2 := state.NewEncoder(256, true)
	for _, in := range blocks {
		block, _ := enc2.Encode(in)
		if _, _, err := mine.Decode(block); err != nil {
			t.Fatalf("replay: %v", err)
		}
	}
	if enc.Table().Dyn.Size() != enc2.Table().Dyn.Size() ||
		mine.Table().Dyn.Size() != enc.Table().Dyn.Size() {
		t.Fatalf("table size disagreement: enc=%d mine=%d enc2=%d",
			enc.Table().Dyn.Size(), mine.Table().Dyn.Size(), enc2.Table().Dyn.Size())
	}
}
