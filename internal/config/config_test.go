package config_test

import (
	"os"
	"path/filepath"
	"testing"

	"berd/internal/config"
	"berd/internal/harness"
)

func TestDefaultWhenFileMissing(t *testing.T) {
	h := harness.New(t)
	cfg, err := config.Load(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	h.ExpectEqual("listen", cfg.Listen, "127.0.0.1:8971")
	h.ExpectEqual("max_depth", cfg.Limits.MaxDepth, 32)
	h.ExpectEqual("allow_indefinite", cfg.Limits.AllowIndefinite, true)
}

func TestFileAndEnvOverrides(t *testing.T) {
	h := harness.New(t)
	path := filepath.Join(t.TempDir(), "berd.json")
	if err := os.WriteFile(path, []byte(`{
		"listen": "127.0.0.1:9000",
		"db_path": "x.db",
		"limits": {"max_depth": 8, "allow_indefinite": false}
	}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("BERD_LISTEN", "127.0.0.1:9001")
	t.Setenv("BERD_DB_PATH", "env-override.db")
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	h.ExpectEqual("env listen wins", cfg.Listen, "127.0.0.1:9001")
	h.ExpectEqual("env db_path wins", cfg.DBPath, "env-override.db")
	h.ExpectEqual("file max_depth", cfg.Limits.MaxDepth, 8)
	h.ExpectEqual("file indefinite off", cfg.Limits.AllowIndefinite, false)
	h.ExpectEqual("default fills max_nodes", cfg.Limits.MaxNodes, 10000)
}

func TestBadJSON(t *testing.T) {
	h := harness.New(t)
	path := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(path, []byte(`{`), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := config.Load(path)
	if err == nil {
		t.Fatal("expected parse error")
	}
	h.Step("verdict=PASS basis=invalid JSON rejected: %v", err)
}

func TestUnreadablePath(t *testing.T) {
	h := harness.New(t)
	// A directory exists but cannot be read as a file.
	_, err := config.Load(t.TempDir())
	if err == nil {
		t.Fatal("expected read error for directory path")
	}
	h.Step("verdict=PASS basis=unreadable config surfaced: %v", err)
}
