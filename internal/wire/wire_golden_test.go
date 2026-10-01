package wire_test

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"coapblockwise/internal/wire"
)

// goldenFile is produced by scripts/gen_golden.py, an independent Python
// encoder. The Go codec never generates these expected bytes.
type goldenFile struct {
	RFC   []string      `json:"rfc"`
	Cases []goldenCase  `json:"cases"`
}

type goldenCase struct {
	Name   string       `json:"name"`
	Note   string       `json:"note"`
	Hex    string       `json:"hex"`
	Parsed goldenParsed `json:"parsed"`
}

type goldenParsed struct {
	Type       string         `json:"type"`
	Code       string         `json:"code"`
	MID        uint16         `json:"mid"`
	TokenHex   string         `json:"token_hex"`
	PayloadHex string         `json:"payload_hex"`
	Options    []goldenOption `json:"options"`
}

type goldenOption struct {
	Number int    `json:"number"`
	Name   string `json:"name"`
	ValueHex string `json:"value_hex"`
	Block  *struct {
		Num  int  `json:"num"`
		More bool `json:"more"`
		SZX  int  `json:"szx"`
		Size int  `json:"size"`
	} `json:"block"`
}

func loadGolden(t *testing.T) []goldenCase {
	t.Helper()
	path := filepath.Join("..", "..", "test", "testdata", "golden",
		"wire_vectors.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read golden vectors (run python3 scripts/gen_golden.py): %v", err)
	}
	var gf goldenFile
	if err := json.Unmarshal(raw, &gf); err != nil {
		t.Fatalf("parse golden vectors: %v", err)
	}
	return gf.Cases
}

func TestDecodeGoldenVectors(t *testing.T) {
	for _, tc := range loadGolden(t) {
		t.Run(tc.Name, func(t *testing.T) {
			dgram, err := hex.DecodeString(tc.Hex)
			if err != nil {
				t.Fatalf("fixture hex invalid: %v", err)
			}
			m, err := wire.Decode(dgram)
			if err != nil {
				t.Fatalf("Decode rejected a golden vector: %v", err)
			}
			if m.Type.String() != tc.Parsed.Type {
				t.Errorf("type = %s, want %s", m.Type, tc.Parsed.Type)
			}
			if m.Code.String() != tc.Parsed.Code {
				t.Errorf("code = %s, want %s", m.Code, tc.Parsed.Code)
			}
			if m.MessageID != tc.Parsed.MID {
				t.Errorf("mid = %d, want %d", m.MessageID, tc.Parsed.MID)
			}
			if got := hex.EncodeToString(m.Token); got != tc.Parsed.TokenHex {
				t.Errorf("token = %q, want %q (token must be independent of MID)",
					got, tc.Parsed.TokenHex)
			}
			if got := hex.EncodeToString(m.Payload); got != tc.Parsed.PayloadHex {
				t.Errorf("payload = %x..., want %s...", truncate(m.Payload),
					truncateHex(tc.Parsed.PayloadHex))
			}
			if len(m.Options) != len(tc.Parsed.Options) {
				t.Fatalf("options count = %d, want %d",
					len(m.Options), len(tc.Parsed.Options))
			}
			for i, want := range tc.Parsed.Options {
				got := m.Options[i]
				if got.Number != want.Number {
					t.Errorf("option[%d] number = %d, want %d", i, got.Number, want.Number)
				}
				if gv := hex.EncodeToString(got.Value); gv != want.ValueHex {
					t.Errorf("option[%d] (%s) value = %s, want %s",
						i, want.Name, gv, want.ValueHex)
				}
				if want.Block != nil {
					b, err := wire.DecodeBlock(got.Value)
					if err != nil {
						t.Fatalf("option[%d] block decode: %v", i, err)
					}
					if b.Num != want.Block.Num || b.More != want.Block.More ||
						b.SZX != want.Block.SZX || b.Size() != want.Block.Size {
						t.Errorf("block = %s, want num=%d more=%v szx=%d size=%d",
							b, want.Block.Num, want.Block.More,
							want.Block.SZX, want.Block.Size)
					}
				}
			}
		})
	}
}

// TestEncodeGoldenVectors verifies Go produces byte-identical datagrams to the
// independent Python encoder for the same semantic message.
func TestEncodeGoldenVectors(t *testing.T) {
	for _, tc := range loadGolden(t) {
		t.Run(tc.Name, func(t *testing.T) {
			dgram, err := hex.DecodeString(tc.Hex)
			if err != nil {
				t.Fatalf("fixture hex invalid: %v", err)
			}
			m, err := wire.Decode(dgram)
			if err != nil {
				t.Fatalf("decode golden: %v", err)
			}
			out, err := m.Encode()
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if hex.EncodeToString(out) != tc.Hex {
				t.Errorf("re-encoded bytes differ from independent golden\n got %x\nwant %x",
					out, dgram)
			}
		})
	}
}

func truncate(b []byte) []byte {
	if len(b) > 8 {
		return b[:8]
	}
	return b
}

func truncateHex(s string) string {
	if len(s) > 16 {
		return s[:16] + "..."
	}
	return s
}
