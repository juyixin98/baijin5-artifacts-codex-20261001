package fixtures_test

import (
	"bytes"
	"crypto/sha256"
	"os"
	"testing"

	"coaplab/internal/fixtures"
)

func TestDeterministicBody_StableAndFormulaic(t *testing.T) {
	a := fixtures.DeterministicBody("seed", 100)
	b := fixtures.DeterministicBody("seed", 100)
	if !bytes.Equal(a, b) {
		t.Fatal("same seed/length must produce identical bytes")
	}
	if len(a) != 100 {
		t.Fatalf("length = %d", len(a))
	}
	// Independently verify the first 32 bytes equal sha256("seed:0").
	h := sha256.Sum256([]byte("seed:0"))
	if !bytes.Equal(a[:32], h[:]) {
		t.Fatal("first block does not match documented formula")
	}
	// Different seed diverges.
	if bytes.Equal(a, fixtures.DeterministicBody("other", 100)) {
		t.Fatal("different seeds must differ")
	}
}

func TestLoad_CommittedFixtureDocument(t *testing.T) {
	doc, err := fixtures.Load("../../test/fixtures/data/resources.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(doc.Resources) < 8 {
		t.Fatalf("fixture resources = %d", len(doc.Resources))
	}
	found := false
	for _, r := range doc.Resources {
		body, err := r.Body()
		if err != nil {
			t.Fatalf("decode %s: %v", r.Path, err)
		}
		switch r.Path {
		case "hello":
			if string(body) != "Hello, CoAP!" {
				t.Fatalf("hello fixture = %q", body)
			}
			found = true
		case "cd/3073b":
			if len(body) != 3073 {
				t.Fatalf("3073 fixture len = %d", len(body))
			}
		}
	}
	if !found {
		t.Fatal("hello fixture missing")
	}
}

func TestLoad_RejectsBadDocument(t *testing.T) {
	if _, err := fixtures.Load("testdata/does-not-exist.json"); err == nil {
		t.Fatal("missing file must error")
	}
	// A path declared inside the test module temp dir with invalid hex.
	dir := t.TempDir()
	p := dir + "/bad.json"
	if err := writeFile(p, `{"resources":[{"path":"x","body_hex":"zz"}]}`); err != nil {
		t.Fatal(err)
	}
	if _, err := fixtures.Load(p); err == nil {
		t.Fatal("invalid hex must be rejected")
	}
}

func writeFile(path, body string) error {
	return os.WriteFile(path, []byte(body), 0o600)
}
