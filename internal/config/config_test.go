package config_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"smtpsink/internal/config"
)

func TestDefault_IsLoopbackOnly(t *testing.T) {
	cfg := config.Default()
	if err := cfg.Validate(); err != nil {
		t.Fatalf("defaults must be valid: %v", err)
	}
	if cfg.Listen.Host != "127.0.0.1" {
		t.Fatalf("default host = %q, want loopback", cfg.Listen.Host)
	}
}

func TestValidate_RejectsNonLoopback(t *testing.T) {
	cases := [][2]string{
		{"0.0.0.0", "must bind loopback only"},
		{"8.8.8.8", "public address rejected"},
		{"", "empty host rejected"},
	}
	for _, tc := range cases {
		cfg := config.Default()
		cfg.Listen.Host = tc[0]
		if err := cfg.Validate(); err == nil {
			t.Fatalf("%s: expected rejection", tc[1])
		}
	}
	// ::1 and localhost remain allowed.
	for _, ok := range []string{"::1", "localhost"} {
		cfg := config.Default()
		cfg.Listen.Host = ok
		if err := cfg.Validate(); err != nil {
			t.Fatalf("%s should be allowed: %v", ok, err)
		}
	}
}

func TestLoad_MissingFileGivesDefaults(t *testing.T) {
	cfg, err := config.Load(filepath.Join(t.TempDir(), "absent.json"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Listen.Port != 2525 {
		t.Fatalf("default port = %d", cfg.Listen.Port)
	}
}

func TestLoad_OverridesAndRejectsBadLimits(t *testing.T) {
	dir := t.TempDir()

	good := config.Default()
	good.Listen.Port = 2600
	b, _ := json.Marshal(good)
	p := filepath.Join(dir, "good.json")
	if err := os.WriteFile(p, b, 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Listen.Port != 2600 {
		t.Fatalf("override port = %d", cfg.Listen.Port)
	}

	bad := `{"limits": {"command_line_bytes": -1}}`
	bp := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(bp, []byte(bad), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := config.Load(bp); err == nil || !strings.Contains(err.Error(), "positive") {
		t.Fatalf("expected positive-limit error, got %v", err)
	}
}

func TestLoad_MalformedJSON(t *testing.T) {
	p := filepath.Join(t.TempDir(), "broken.json")
	if err := os.WriteFile(p, []byte("{ not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := config.Load(p); err == nil || !strings.Contains(err.Error(), "parse") {
		t.Fatalf("expected parse error, got %v", err)
	}
	if c, err := config.Load(""); err != nil || c.Listen.Port != 2525 {
		t.Fatalf("empty path should yield defaults, got %+v %v", c, err)
	}
}

func TestAddress(t *testing.T) {
	cfg := config.Default()
	if got := cfg.Address(); got != "127.0.0.1:2525" {
		t.Fatalf("Address() = %q", got)
	}
}

func TestValidate_Branches(t *testing.T) {
	cases := []func(c *config.Config){
		func(c *config.Config) { c.Listen.Port = 0 },
		func(c *config.Config) { c.Hostname = " " },
		func(c *config.Config) { c.Storage.Path = "" },
		func(c *config.Config) { c.LocalDomains = nil },
		func(c *config.Config) { c.Limits.DataLineBytes = 0 },
		func(c *config.Config) { c.Limits.Recipients = 0 },
		func(c *config.Config) { c.Timeouts.IdleMS = 0 },
	}
	for i, mutate := range cases {
		cfg := config.Default()
		mutate(&cfg)
		if err := cfg.Validate(); err == nil {
			t.Fatalf("case %d: expected validation error", i)
		}
	}
}
