package config_test

import (
	"path/filepath"
	"testing"

	"rlmod/internal/config"
)

func TestLoadRepoConfig(t *testing.T) {
	c, err := config.Load(filepath.Join("..", "..", "config", "rlverify.json"))
	if err != nil {
		t.Fatal(err)
	}
	if c.FingerprintSchema != "fp-v1" {
		t.Fatalf("schema = %s", c.FingerprintSchema)
	}
	if got := c.Scenarios["inline-const"].New; got == "" {
		t.Fatal("inline-const scenario missing")
	}
	if c.ErrorFixtures["type_mismatch"] == "" {
		t.Fatal("type_mismatch fixture missing")
	}
}
