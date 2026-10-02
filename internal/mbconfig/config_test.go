package mbconfig

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultIsValid(t *testing.T) {
	if err := Default().Validate(); err != nil {
		t.Fatalf("default config invalid: %v", err)
	}
}

func TestLoadRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cfg.json")
	if err := os.WriteFile(path, []byte(`{
		"listen": "127.0.0.1:2502",
		"unit_ids": [1, 7],
		"register_count": 64,
		"db_path": ":memory:",
		"log_path": "-",
		"request_timeout_ms": 1500
	}`), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.Listen != "127.0.0.1:2502" || len(cfg.UnitIDs) != 2 || cfg.RegisterCount != 64 {
		t.Fatalf("unexpected config: %+v", cfg)
	}
}

func TestLoadRejectsInvalid(t *testing.T) {
	cases := map[string]string{
		"no_units":     `{"listen":"x","unit_ids":[],"register_count":8,"request_timeout_ms":1}`,
		"dup_units":    `{"listen":"x","unit_ids":[1,1],"register_count":8,"request_timeout_ms":1}`,
		"zero_regs":    `{"listen":"x","unit_ids":[1],"register_count":0,"request_timeout_ms":1}`,
		"zero_timeout": `{"listen":"x","unit_ids":[1],"register_count":8,"request_timeout_ms":0}`,
		"empty_listen": `{"listen":"","unit_ids":[1],"register_count":8,"request_timeout_ms":1}`,
	}
	for name, body := range cases {
		path := filepath.Join(t.TempDir(), "cfg.json")
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		if _, err := Load(path); err == nil {
			t.Fatalf("%s: expected validation error", name)
		}
	}
	if _, err := Load("/nonexistent/cfg.json"); err == nil {
		t.Fatal("expected error for missing file")
	}
}
