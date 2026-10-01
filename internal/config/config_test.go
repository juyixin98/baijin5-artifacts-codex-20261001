package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"sockswhitelist/internal/policy"
)

func TestLoadValid(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "proxy.yaml")
	content := `
listen: "127.0.0.1:1080"
database: ":memory:"
auth:
  required: true
limits:
  max_concurrent_connections: 8
  handshake_timeout: 3s
  resolve_timeout: 1s
  dial_timeout: 2500ms
  idle_timeout: 90s
  max_bytes_up: 1024
  max_bytes_down: 2048
log:
  level: debug
rules:
  - kind: cidr
    host: "127.0.0.0/8"
    ports: [0]
  - kind: domain
    host: "*.local.test"
    ports: [8080]
`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.Limits.HandshakeTimeout != 3*time.Second {
		t.Fatalf("handshake timeout = %v", cfg.Limits.HandshakeTimeout)
	}
	if cfg.Limits.DialTimeout != 2500*time.Millisecond {
		t.Fatalf("dial timeout = %v", cfg.Limits.DialTimeout)
	}
	if cfg.Limits.MaxBytesDown != 2048 || !cfg.Auth.Required {
		t.Fatalf("limits/auth wrong: %+v", cfg)
	}
	if len(cfg.Rules) != 2 {
		t.Fatalf("rules = %d", len(cfg.Rules))
	}
}

func TestValidateRejectsBadConfig(t *testing.T) {
	base := Default()
	base.Limits.MaxConcurrentConnections = 0
	if err := base.Validate(); err == nil {
		t.Fatal("zero concurrency must be invalid")
	}
	base = Default()
	base.Limits.IdleTimeout = 0
	if err := base.Validate(); err == nil {
		t.Fatal("zero idle timeout must be invalid")
	}
	base = Default()
	base.Log.Level = "verbose"
	if err := base.Validate(); err == nil {
		t.Fatal("unknown log level must be invalid")
	}
	base = Default()
	base.Rules = []policy.Rule{{Kind: "bogus", Host: "x"}}
	if err := base.Validate(); err == nil {
		t.Fatal("unknown rule kind must be invalid")
	}
}
