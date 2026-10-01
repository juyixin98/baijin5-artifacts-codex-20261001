package config_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"socks5d.local/socks5d/internal/config"
)

func writeTemp(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "cfg.json")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatalf("write cfg: %v", err)
	}
	return p
}

func TestLoad_Valid(t *testing.T) {
	p := writeTemp(t, `{
		"listen": "127.0.0.1:1080",
		"handshake_timeout": "2s",
		"dial_timeout": "3s",
		"idle_timeout": "30s",
		"shutdown_timeout": "1s",
		"max_connections": 10,
		"byte_budget_per_connection": 1024,
		"database": {"path": "/tmp/x.db"},
		"rules": [
			{"kind": "cidr", "value": "127.0.0.0/8"},
			{"kind": "domain", "value": "echo.local"}
		]
	}`)
	cfg, err := config.Load(p)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.HandshakeTimeout.Duration != 2*time.Second {
		t.Fatalf("handshake timeout = %v", cfg.HandshakeTimeout)
	}
	if cfg.MaxConnections != 10 || cfg.ByteBudget != 1024 {
		t.Fatalf("bounds not parsed: %+v", cfg)
	}
	// Domain rule defaults to exact mode.
	if cfg.Rules[1].Mode != "exact" {
		t.Fatalf("domain mode = %q want exact", cfg.Rules[1].Mode)
	}
}

func TestValidate_RejectsBadValues(t *testing.T) {
	base := config.Default()
	base.Database.Path = filepath.Join(t.TempDir(), "x.db")

	tests := []struct {
		name   string
		mutate func(*config.Config)
		want   string
	}{
		{"zero_handshake", func(c *config.Config) { c.HandshakeTimeout = config.Duration{} }, "handshake_timeout"},
		{"zero_dial", func(c *config.Config) { c.DialTimeout = config.Duration{} }, "dial_timeout"},
		{"negative_idle", func(c *config.Config) { c.IdleTimeout = config.Duration{-time.Second} }, "idle_timeout"},
		{"zero_max_conn", func(c *config.Config) { c.MaxConnections = 0 }, "max_connections"},
		{"zero_budget", func(c *config.Config) { c.ByteBudget = 0 }, "byte_budget"},
		{"empty_db", func(c *config.Config) { c.Database.Path = "" }, "database.path"},
		{"bad_cidr", func(c *config.Config) { c.Rules = []config.Rule{{Kind: "cidr", Value: "nope"}} }, "invalid CIDR"},
		{"bad_domain_mode", func(c *config.Config) {
			c.Rules = []config.Rule{{Kind: "domain", Value: "a.local", Mode: "wildcard"}}
		}, "domain mode"},
		{"bad_domain_name", func(c *config.Config) {
			c.Rules = []config.Rule{{Kind: "domain", Value: "bad name!"}}
		}, "invalid domain"},
		{"bad_hosts_ip", func(c *config.Config) {
			c.Resolver.Hosts = map[string][]string{"echo.local": {"not-an-ip"}}
		}, "invalid IP"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c := base
			tc.mutate(&c)
			err := c.Validate()
			if err == nil {
				t.Fatal("expected validation error")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("error %q does not mention %q", err.Error(), tc.want)
			}
		})
	}
}

func TestLoad_MissingFile(t *testing.T) {
	if _, err := config.Load(filepath.Join(t.TempDir(), "missing.json")); err == nil {
		t.Fatal("expected error for missing file")
	}
}

func TestLoad_BadJSON(t *testing.T) {
	p := writeTemp(t, `{not json`)
	if _, err := config.Load(p); err == nil {
		t.Fatal("expected parse error")
	}
}

func TestCIDRNormalized(t *testing.T) {
	p := writeTemp(t, `{
		"handshake_timeout":"1s","dial_timeout":"1s",
		"max_connections":2,"byte_budget_per_connection":8,
		"database":{"path":"a.db"},
		"rules":[{"kind":"cidr","value":"127.0.0.1/8"}]
	}`)
	cfg, err := config.Load(p)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.Rules[0].Value != "127.0.0.0/8" {
		t.Fatalf("cidr not masked/normalized: %q", cfg.Rules[0].Value)
	}
}
