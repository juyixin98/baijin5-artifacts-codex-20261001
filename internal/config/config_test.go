package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultIsValid(t *testing.T) {
	if err := Default().Validate(); err != nil {
		t.Fatalf("default config must be valid: %v", err)
	}
}

func TestLoadLayersOnDefaults(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "cfg.json")
	if err := os.WriteFile(path, []byte(`{"listen_addr":"127.0.0.1:9999","send_queue_capacity":8}`), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.ListenAddr != "127.0.0.1:9999" {
		t.Fatalf("listen_addr: got %s", cfg.ListenAddr)
	}
	if cfg.SendQueueCapacity != 8 {
		t.Fatalf("send_queue_capacity: got %d", cfg.SendQueueCapacity)
	}
	// Untouched fields keep defaults.
	if cfg.MaxFrameSize != 16384 {
		t.Fatalf("max_frame_size default: got %d", cfg.MaxFrameSize)
	}
}

func TestLoadRejectsInvalid(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(path, []byte(`{"send_queue_capacity":0}`), 0o600); err != nil {
		t.Fatal(err)
	}
	// 0 means "unset" for the loader's layering, so force a negative value.
	if err := os.WriteFile(path, []byte(`{"send_queue_capacity":-3}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("expected validation error for negative send_queue_capacity")
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load(filepath.Join(t.TempDir(), "nope.json")); err == nil {
		t.Fatal("expected error for missing config file")
	}
}
