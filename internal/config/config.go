// Package config loads service configuration from a JSON file with
// environment-variable overrides.
package config

import (
	"encoding/json"
	"fmt"
	"os"

	"berd/internal/ber"
)

// Config is the runtime configuration of the BER service.
type Config struct {
	// Listen is the HTTP bind address, e.g. "127.0.0.1:8971".
	Listen string `json:"listen"`
	// DBPath is the SQLite audit database file (":memory:" allowed).
	DBPath string `json:"db_path"`
	// Limits bounds decoder resource usage.
	Limits ber.Limits `json:"limits"`
}

// Default returns the configuration used when no file is present.
func Default() Config {
	return Config{
		Listen: "127.0.0.1:8971",
		DBPath: "berd-audit.db",
		Limits: ber.DefaultLimits(),
	}
}

// Load reads path if it exists, applies defaults to missing fields, then
// applies BERD_* environment overrides.
func Load(path string) (Config, error) {
	cfg := Default()
	data, err := os.ReadFile(path)
	if err != nil {
		if !os.IsNotExist(err) {
			return cfg, fmt.Errorf("read config %s: %w", path, err)
		}
	} else {
		if err := json.Unmarshal(data, &cfg); err != nil {
			return cfg, fmt.Errorf("parse config %s: %w", path, err)
		}
	}
	if v := os.Getenv("BERD_LISTEN"); v != "" {
		cfg.Listen = v
	}
	if v := os.Getenv("BERD_DB_PATH"); v != "" {
		cfg.DBPath = v
	}
	cfg.Limits = cfg.Limits.WithDefaults()
	return cfg, nil
}
