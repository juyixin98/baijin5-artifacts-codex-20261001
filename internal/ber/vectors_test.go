package ber_test

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"

	"berd/internal/ber"
	"berd/internal/harness"
)

type vectorFile struct {
	Source string `json:"source"`
	OK     []struct {
		Name        string   `json:"name"`
		Hex         string   `json:"hex"`
		Kind        string   `json:"kind"`
		Value       string   `json:"value"`
		UnusedBits  int      `json:"unused_bits"`
		PayloadHex  string   `json:"payload_hex"`
		BitLength   int      `json:"bit_length"`
		Tag         uint64   `json:"tag"`
		Constructed bool     `json:"constructed"`
		ContentHex  string   `json:"content_hex"`
		Indefinite  bool     `json:"indefinite"`
		Children    []string `json:"children"`
	} `json:"ok"`
	Errors []struct {
		Name     string `json:"name"`
		Hex      string `json:"hex"`
		Category string `json:"category"`
		Offset   int    `json:"offset"`
	} `json:"errors"`
}

func loadVectors(t *testing.T) vectorFile {
	t.Helper()
	data, err := os.ReadFile("testdata/vectors.json")
	if err != nil {
		t.Fatalf("load vectors: %v", err)
	}
	var vf vectorFile
	if err := json.Unmarshal(data, &vf); err != nil {
		t.Fatalf("parse vectors: %v", err)
	}
	return vf
}

// TestVectorsOK decodes every hand-written positive vector and asserts the
// decoded value, not merely the absence of an error.
func TestVectorsOK(t *testing.T) {
	h := harness.New(t)
	vf := loadVectors(t)
	h.Step("loaded %d ok vectors, %d error vectors; source=%q",
		len(vf.OK), len(vf.Errors), vf.Source)
	lim := ber.DefaultLimits()
	for _, v := range vf.OK {
		t.Run(v.Name, func(t *testing.T) {
			h := harness.New(t)
			data := harness.MustDecodeHex(t, v.Hex)
			root, err := ber.DecodeAll(data, lim)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			if verr := ber.Validate(root, lim); verr != nil {
				t.Fatalf("validate: %v", verr)
			}
			h.Step("vector=%s input=%s kind=%s", v.Name, v.Hex, v.Kind)
			switch v.Kind {
			case "integer":
				got, gerr := root.Integer(lim)
				if gerr != nil {
					t.Fatalf("integer: %v", gerr)
				}
				h.ExpectEqual("integer value", got.String(), v.Value)
			case "bitstring":
				bs, berr := root.BitString(lim)
				if berr != nil {
					t.Fatalf("bitstring: %v", berr)
				}
				h.ExpectEqual("unused bits", bs.Unused, v.UnusedBits)
				h.ExpectEqual("bit length", bs.BitLength(), v.BitLength)
				h.ExpectBytes("payload", bs.Bytes, v.PayloadHex)
			case "sequence":
				if !root.Is(ber.Universal, ber.TagSequence) || !root.Constructed {
					t.Fatalf("not a SEQUENCE: %+v", root)
				}
				h.ExpectEqual("indefinite flag", root.Indefinite, v.Indefinite)
				assertLeafIntegers(t, h, root, v.Children, lim)
			case "context":
				if root.Class != ber.Context || root.Tag != v.Tag {
					t.Fatalf("class/tag: got %s/%d want context/%d",
						root.Class, root.Tag, v.Tag)
				}
				h.ExpectEqual("constructed", root.Constructed, v.Constructed)
				if v.Constructed {
					assertLeafIntegers(t, h, root, v.Children, lim)
				} else {
					h.ExpectBytes("content", root.Content, v.ContentHex)
				}
			default:
				t.Fatalf("unknown vector kind %q", v.Kind)
			}
		})
	}
}

// TestVectorsErrors asserts the exact failure category and byte offset of
// every hand-written negative vector.
func TestVectorsErrors(t *testing.T) {
	vf := loadVectors(t)
	lim := ber.DefaultLimits()
	for _, v := range vf.Errors {
		t.Run(v.Name, func(t *testing.T) {
			h := harness.New(t)
			data := harness.MustDecodeHex(t, v.Hex)
			_, err := ber.DecodeAll(data, lim)
			if err == nil {
				err = ber.Validate(mustRoot(t, data, lim), lim)
			}
			h.ExpectError(data, err, ber.Category(v.Category), v.Offset)
		})
	}
}

func mustRoot(t *testing.T, data []byte, lim ber.Limits) *ber.Node {
	t.Helper()
	root, err := ber.DecodeAll(data, lim)
	if err != nil {
		t.Fatalf("decode unexpectedly failed: %v", err)
	}
	return root
}

// assertLeafIntegers collects the INTEGER leaves of a tree in depth-first
// order and compares them with the expected decimal strings.
func assertLeafIntegers(t *testing.T, h *harness.Logger, root *ber.Node, want []string, lim ber.Limits) {
	t.Helper()
	var got []string
	var walk func(n *ber.Node)
	walk = func(n *ber.Node) {
		if n.Is(ber.Universal, ber.TagInteger) && !n.Constructed {
			v, err := n.Integer(lim)
			if err != nil {
				t.Fatalf("leaf integer: %v", err)
			}
			got = append(got, v.String())
			return
		}
		for _, c := range n.Children {
			walk(c)
		}
	}
	walk(root)
	h.ExpectEqual("leaf count", len(got), len(want))
	for i := range want {
		h.ExpectEqual("leaf integer", got[i], want[i])
	}
}

// TestEOCOnlyTerminatesIndefinite pins the core EOC rule: an EOC pair closes
// exactly the innermost open indefinite-length value and is never consumed as
// ordinary zero-length data.
func TestEOCOnlyTerminatesIndefinite(t *testing.T) {
	h := harness.New(t)
	lim := ber.DefaultLimits()

	// Two EOC pairs terminate the two nested indefinite SEQUENCEs; the
	// decoded INTEGER survives intact.
	data := harness.MustDecodeHex(t, "3080308002010500000000")
	root, err := ber.DecodeAll(data, lim)
	if err != nil {
		t.Fatalf("decode nested indefinite: %v", err)
	}
	inner := root.Children[0]
	v, verr := inner.Children[0].Integer(lim)
	if verr != nil {
		t.Fatalf("integer: %v", verr)
	}
	h.ExpectEqual("nested indefinite integer", v.String(), "5")
	h.ExpectEqual("outer indefinite", root.Indefinite, true)
	h.ExpectEqual("inner indefinite", inner.Indefinite, true)

	// An EOC where a value is expected inside a definite SEQUENCE is an
	// error, not a zero-length child.
	h.ExpectError(harness.MustDecodeHex(t, "30020000"),
		mustErr(t, "30020000", lim), ber.CatEOC, 2)

	// Leftover EOC after the indefinite value is closed is trailing data at
	// the top level, reported at the offset where it starts.
	_, _, derr := ber.Decode(harness.MustDecodeHex(t, "308000000000"), lim)
	if derr != nil {
		t.Fatalf("first value should decode: %v", derr)
	}
	_, aerr := ber.DecodeAll(harness.MustDecodeHex(t, "308000000000"), lim)
	h.ExpectError(harness.MustDecodeHex(t, "308000000000"), aerr, ber.CatSyntax, 4)
}

func mustErr(t *testing.T, hexIn string, lim ber.Limits) *ber.Error {
	t.Helper()
	data, _ := hex.DecodeString(hexIn)
	_, err := ber.DecodeAll(data, lim)
	if err == nil {
		t.Fatalf("expected error for %s", hexIn)
	}
	return err
}
