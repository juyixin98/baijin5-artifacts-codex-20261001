package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDefaultIsValid(t *testing.T) {
	cfg, err := Load("")
	if err != nil {
		t.Fatalf("defaults must validate: %v", err)
	}
	if cfg.Limits.MaxDepth != 32 || cfg.Limits.MaxIntegerBytes != 4096 {
		t.Fatalf("unexpected normalized limits: %+v", cfg.Limits)
	}
}

func TestLoadFromFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "c.json")
	content := `{
		"service": {"name":"x","version":"9.9.9"},
		"http": {"listen_addr":"127.0.0.1:9999","max_body_bytes":1024},
		"storage": {"driver":"modernc-sqlite","dsn":"file:test.db","request_log_table":"audit_log"},
		"logging": {"level":"warn","format":"text","output_path":"stdout"},
		"limits": {"max_depth": 4, "max_integer_bytes": 16}
	}`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.HTTP.ListenAddr != "127.0.0.1:9999" || cfg.HTTP.MaxBodyBytes != 1024 {
		t.Fatalf("http override failed: %+v", cfg.HTTP)
	}
	if cfg.Limits.MaxDepth != 4 {
		t.Fatalf("limit override failed: %+v", cfg.Limits)
	}
	if cfg.Limits.MaxIntegerBytes != 16 {
		t.Fatalf("limit normalize failed: %+v", cfg.Limits)
	}
	if cfg.Storage.RequestLogTable != "audit_log" {
		t.Fatalf("storage override failed: %+v", cfg.Storage)
	}
}

func TestEnvOverrides(t *testing.T) {
	t.Setenv("BERSERV_LISTEN_ADDR", "127.0.0.1:7777")
	t.Setenv("BERSERV_MAX_DEPTH", "3")
	t.Setenv("BERSERV_MAX_INTEGER_BYTES", "10")
	cfg, err := Load("")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.HTTP.ListenAddr != "127.0.0.1:7777" {
		t.Fatalf("addr env override: %s", cfg.HTTP.ListenAddr)
	}
	if cfg.Limits.MaxDepth != 3 || cfg.Limits.MaxIntegerBytes != 10 {
		t.Fatalf("limit env overrides: %+v", cfg.Limits)
	}
}

func TestRejectsUnsafeTable(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "c.json")
	for _, bad := range []string{"x; DROP TABLE y", "1abc", "a-b", "name`ls`"} {
		content := `{"storage":{"driver":"modernc-sqlite","request_log_table":` +
			quoteJSON(bad) + `}}`
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := Load(path); err == nil {
			t.Fatalf("unsafe table name %q accepted", bad)
		}
	}
}

func TestRejectsBadDriver(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "c.json")
	content := `{"storage":{"driver":"postgres","request_log_table":"r"}}`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "driver") {
		t.Fatalf("expected driver validation error, got %v", err)
	}
}

func quoteJSON(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
