package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultIsValid(t *testing.T) {
	if err := Default().Validate(); err != nil {
		t.Fatalf("defaults invalid: %v", err)
	}
	if Default().Hpack.TableSize != 4096 {
		t.Fatal("default table size should be 4096")
	}
}

func TestLoadOverridesDefaults(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "c.json")
	body := `{
	  "server": {"listen": "127.0.0.1:9999"},
	  "hpack": {"table_size": 256, "max_header_fields": 10},
	  "storage": {"sqlite_path": "/tmp/x.db"}
	}`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Server.Listen != "127.0.0.1:9999" {
		t.Fatalf("listen = %q", cfg.Server.Listen)
	}
	if cfg.Hpack.TableSize != 256 || cfg.Hpack.MaxHeaderFields != 10 {
		t.Fatalf("hpack override failed: %+v", cfg.Hpack)
	}
	// Untouched defaults survive.
	if cfg.Hpack.MaxStringLen != 64*1024 {
		t.Fatalf("default max string len lost: %d", cfg.Hpack.MaxStringLen)
	}
}

func TestValidateRejectsBadConfig(t *testing.T) {
	cases := map[string]Config{
		"empty listen":     {Server: ServerConfig{Listen: ""}, Hpack: HpackConfig{TableSize: 1}},
		"zero table size":  {Server: ServerConfig{Listen: "x"}, Hpack: HpackConfig{TableSize: 0}},
		"cert without key": {Server: ServerConfig{Listen: "x", CertFile: "c"}, Hpack: HpackConfig{TableSize: 1}},
	}
	for name, cfg := range cases {
		if err := cfg.Validate(); err == nil {
			t.Errorf("%s: expected validation error", name)
		}
	}
}

func TestLoadRejectsMalformedJSON(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("expected parse error")
	}
}
