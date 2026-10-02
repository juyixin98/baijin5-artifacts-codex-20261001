// Package fixtures loads synthetic sample resources used to seed the
// controlled service and as golden data by the test suites. All content is
// deterministically generated locally — there is no real business data.
package fixtures

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
)

// ResourceFixture is one synthetic resource on disk.
type ResourceFixture struct {
	Path          string `json:"path"`
	ContentFormat uint16 `json:"content_format"`
	// BodyHex holds the representation bytes (hex) so fixtures stay
	// pure-ASCII and diff-friendly.
	BodyHex string `json:"body_hex"`
	// Note describes how the bytes were generated (provenance).
	Note string `json:"note"`
}

// File is the on-disk fixture document.
type File struct {
	GeneratedBy string            `json:"generated_by"`
	Description string            `json:"description"`
	Resources   []ResourceFixture `json:"resources"`
}

// Load reads and decodes a fixture document.
func Load(path string) (File, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return File{}, fmt.Errorf("fixtures: load %s: %w", path, err)
	}
	var f File
	if err := json.Unmarshal(raw, &f); err != nil {
		return File{}, fmt.Errorf("fixtures: parse %s: %w", path, err)
	}
	if len(f.Resources) == 0 {
		return File{}, fmt.Errorf("fixtures: %s contains no resources", path)
	}
	for _, r := range f.Resources {
		if _, err := hex.DecodeString(r.BodyHex); err != nil {
			return File{}, fmt.Errorf("fixtures: resource %q has bad body_hex: %w", r.Path, err)
		}
	}
	return f, nil
}

// Body decodes one fixture body.
func (r ResourceFixture) Body() ([]byte, error) { return hex.DecodeString(r.BodyHex) }

// DeterministicBody builds a reproducible pseudo-body of exactly n bytes:
// a repeating counter+digest pattern. Bodies built this way are easy to
// verify independently (the oracle test recomputes the same formula).
func DeterministicBody(seed string, n int) []byte {
	out := make([]byte, 0, n)
	counter := uint64(0)
	for len(out) < n {
		h := sha256.Sum256([]byte(fmt.Sprintf("%s:%d", seed, counter)))
		out = append(out, h[:]...)
		counter++
	}
	return out[:n]
}
