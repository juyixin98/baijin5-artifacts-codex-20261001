package compat

import (
	"bytes"
	"encoding/hex"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	xhpack "golang.org/x/net/http2/hpack"

	"hpacklab.local/hpack"
)

// oracleLimits are deliberately generous: the point of the oracle tests is
// wire interoperability, not our DoS budget (that has its own tests).
func oracleLimits() hpack.Limits {
	return hpack.Limits{MaxStringLen: 4 << 20, MaxHeaderBlockEmitted: 64 << 20, MaxHeaderFields: 10000}
}

// decodeWithXNet drives the independent x/net decoder over one story,
// mirroring the per-case SETTINGS_HEADER_TABLE_SIZE changes. DecodeFull is
// used (rather than the streaming Write) so each call ends the header block
// and x/net resets its "size update must lead the block" gate between
// cases, exactly as our block-oriented decoder does.
func decodeWithXNet(t *testing.T, s story) [][]xhpack.HeaderField {
	t.Helper()
	var result [][]xhpack.HeaderField
	xd := xhpack.NewDecoder(defaultTableSize, nil)
	for _, c := range s.Cases {
		if c.HeaderTableSize != nil {
			xd.SetAllowedMaxDynamicTableSize(*c.HeaderTableSize)
		}
		wire, err := hex.DecodeString(strings.TrimSpace(c.Wire))
		if err != nil {
			t.Fatalf("bad hex: %v", err)
		}
		got, err := xd.DecodeFull(wire)
		if err != nil {
			t.Fatalf("x/net rejected golden wire: %v", err)
		}
		result = append(result, got)
	}
	return result
}

// TestOracleAgreesWithXNet decodes every golden wire with BOTH our decoder
// and the independent golang.org/x/net decoder, asserting identical fields
// in identical order. A mismatch here cannot come from a shared bug because
// the two implementations share no code.
func TestOracleAgreesWithXNet(t *testing.T) {
	root := filepath.Join("testdata", "corpus")
	dirs := []string{
		"nghttp2", "nghttp2-change-table-size", "nghttp2-16384-4096",
		"python-hpack", "node-http2-hpack", "swift-nio-hpack-huffman", "go-hpack",
	}
	total := 0
	for _, impl := range dirs {
		impl := impl
		t.Run(impl, func(t *testing.T) {
			matches, _ := filepath.Glob(filepath.Join(root, impl, "story_*.json"))
			sort.Strings(matches)
			for _, m := range matches {
				s := loadStory(t, m)
				xdecoded := decodeWithXNet(t, s)
				d := hpack.NewDecoder(hpack.DecoderOptions{MaxTableSize: defaultTableSize, Limits: oracleLimits()})
				for i, c := range s.Cases {
					if c.HeaderTableSize != nil {
						d.SetAllowedMaxTableSize(*c.HeaderTableSize)
					}
					wire, _ := hex.DecodeString(strings.TrimSpace(c.Wire))
					got, err := d.DecodeBlock(wire)
					if err != nil {
						t.Fatalf("%s/%s case %d: our decoder errored: %v", impl, filepath.Base(m), i, err)
					}
					want := xdecoded[i]
					if len(got) != len(want) {
						t.Fatalf("%s/%s case %d: %d fields, x/net got %d", impl, filepath.Base(m), i, len(got), len(want))
					}
					for j := range want {
						if got[j].Name != want[j].Name || got[j].Value != want[j].Value {
							t.Fatalf("%s/%s case %d field %d = %q:%q, x/net = %q:%q",
								impl, filepath.Base(m), i, j, got[j].Name, got[j].Value, want[j].Name, want[j].Value)
						}
					}
					total++
				}
			}
		})
	}
	t.Logf("our decoder and x/net agreed on %d header blocks from 7 independent encoders", total)
}

// TestXNetDecodesOurEncoding feeds raw-data header sets through OUR
// encoder, then decodes the bytes with x/net. The expected headers come
// from the public corpus, not from our implementation.
func TestXNetDecodesOurEncoding(t *testing.T) {
	root := filepath.Join("testdata", "corpus", "raw-data")
	matches, _ := filepath.Glob(filepath.Join(root, "story_*.json"))
	sort.Strings(matches)
	total := 0
	for _, m := range matches {
		s := loadStory(t, m)
		enc := hpack.NewEncoder(hpack.EncoderOptions{MaxTableSize: defaultTableSize, Huffman: true})
		xd := xhpack.NewDecoder(defaultTableSize, nil)
		for i, c := range s.Cases {
			fields := goldenFields(c)
			wire := enc.EncodeBlock(fields)
			got, err := xd.DecodeFull(wire)
			if err != nil {
				t.Fatalf("%s case %d: x/net rejected our wire %x: %v", filepath.Base(m), i, wire, err)
			}
			if len(got) != len(fields) {
				t.Fatalf("%s case %d: x/net saw %d fields, want %d", filepath.Base(m), i, len(got), len(fields))
			}
			for j := range fields {
				if got[j].Name != fields[j].Name || got[j].Value != fields[j].Value {
					t.Fatalf("%s case %d field %d: x/net got %q:%q, want %q:%q",
						filepath.Base(m), i, j, got[j].Name, got[j].Value, fields[j].Name, fields[j].Value)
				}
			}
			total++
		}
	}
	t.Logf("x/net successfully decoded %d blocks our encoder produced from raw-data", total)
}

// TestOurDecoderAcceptsXNetEncoding is the reverse direction: x/net encodes
// the raw-data sets and our decoder must recover exactly those fields.
func TestOurDecoderAcceptsXNetEncoding(t *testing.T) {
	root := filepath.Join("testdata", "corpus", "raw-data")
	matches, _ := filepath.Glob(filepath.Join(root, "story_*.json"))
	sort.Strings(matches)
	total := 0
	for _, m := range matches {
		s := loadStory(t, m)
		var buf bytes.Buffer
		xenc := xhpack.NewEncoder(&buf)
		d := hpack.NewDecoder(hpack.DecoderOptions{MaxTableSize: defaultTableSize, Limits: oracleLimits()})
		for i, c := range s.Cases {
			buf.Reset()
			want := goldenFields(c)
			for _, f := range want {
				if err := xenc.WriteField(xhpack.HeaderField{Name: f.Name, Value: f.Value}); err != nil {
					t.Fatal(err)
				}
			}
			got, err := d.DecodeBlock(buf.Bytes())
			if err != nil {
				t.Fatalf("%s case %d: our decoder rejected x/net wire %x: %v", filepath.Base(m), i, buf.Bytes(), err)
			}
			if len(got) != len(want) {
				t.Fatalf("%s case %d: %d fields, want %d", filepath.Base(m), i, len(got), len(want))
			}
			for j := range want {
				if got[j].Name != want[j].Name || got[j].Value != want[j].Value {
					t.Fatalf("%s case %d field %d = %q:%q, want %q:%q",
						filepath.Base(m), i, j, got[j].Name, got[j].Value, want[j].Name, want[j].Value)
				}
			}
			total++
		}
	}
	t.Logf("our decoder decoded %d blocks produced by x/net's encoder", total)
}

// TestMalformedAgreement feeds deliberately invalid bytes to both decoders
// and requires both to refuse them. x/net is driven via DecodeFull so a
// truncated tail surfaces as its "truncated headers" block-end error rather
// than being silently buffered for a hypothetical continuation.
func TestMalformedAgreement(t *testing.T) {
	vectors := map[string][]byte{
		"index-zero":        {0x80},
		"index-out-range":   {0xbe},
		"huffman-bad-pad":   {0x40, 0x81, 0xff},
		"truncated-integer": {0xff},
		"truncated-string":  {0x40, 0x0a},
		"size-too-large":    {0x3f, 0xe1, 0x9f, 0x00},
	}
	for name, wire := range vectors {
		ours := hpack.NewDecoder(hpack.DecoderOptions{MaxTableSize: 256, Limits: oracleLimits()})
		if _, err := ours.DecodeBlock(wire); err == nil {
			t.Errorf("%s: our decoder accepted invalid input", name)
		}
		xd := xhpack.NewDecoder(256, nil)
		if _, err := xd.DecodeFull(wire); err == nil {
			t.Errorf("%s: x/net accepted input that we reject", name)
		}
	}
}
