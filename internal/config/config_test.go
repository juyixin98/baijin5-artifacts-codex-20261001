package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDefaultIsValid(t *testing.T) {
	if err := Default().Validate(); err != nil {
		t.Fatalf("default config invalid: %v", err)
	}
}

func TestLoadMergesOverDefaults(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cfg.json")
	if err := os.WriteFile(path, []byte(`{"listen": "127.0.0.1:2526", "max_recipients": 3}`), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Listen != "127.0.0.1:2526" {
		t.Fatalf("listen = %q", cfg.Listen)
	}
	if cfg.MaxRecipients != 3 {
		t.Fatalf("max_recipients = %d", cfg.MaxRecipients)
	}
	// Untouched fields keep defaults.
	if cfg.MaxSessions != 32 || cfg.Hostname != "smtpsink.local" {
		t.Fatalf("defaults not preserved: %+v", cfg)
	}
}

func TestValidateRejectsBadValues(t *testing.T) {
	cases := map[string]func(*Config){
		"empty listen":       func(c *Config) { c.Listen = "" },
		"empty hostname":     func(c *Config) { c.Hostname = "" },
		"no domains":         func(c *Config) { c.LocalDomains = nil },
		"bad domain":         func(c *Config) { c.LocalDomains = []string{"notadomain"} },
		"line cap below RFC": func(c *Config) { c.MaxLineBytes = 100 },
		"zero message cap":   func(c *Config) { c.MaxMessageBytes = 0 },
		"zero recipients":    func(c *Config) { c.MaxRecipients = 0 },
		"zero sessions":      func(c *Config) { c.MaxSessions = 0 },
		"empty storage dir":  func(c *Config) { c.StorageDir = "" },
	}
	for name, mutate := range cases {
		cfg := Default()
		mutate(&cfg)
		if err := cfg.Validate(); err == nil {
			t.Errorf("%s: expected validation error", name)
		}
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load(filepath.Join(t.TempDir(), "nope.json")); err == nil {
		t.Fatalf("expected error for missing file")
	}
}

func TestLoadInvalidJSON(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(path, []byte(`{"listen":`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "parse") {
		t.Fatalf("expected parse error, got %v", err)
	}
}
