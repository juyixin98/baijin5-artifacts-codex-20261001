// Package mbconfig loads the fixture's JSON configuration.
package mbconfig

import (
	"encoding/json"
	"fmt"
	"os"
)

// Config is the slave fixture configuration.
type Config struct {
	Listen           string  `json:"listen"`             // e.g. "127.0.0.1:1502"
	UnitIDs          []uint8 `json:"unit_ids"`           // units this slave answers for
	RegisterCount    uint16  `json:"register_count"`     // registers per unit
	DBPath           string  `json:"db_path"`            // SQLite path, ":memory:" allowed
	LogPath          string  `json:"log_path"`           // "" or "-" means stderr
	RequestTimeoutMs int     `json:"request_timeout_ms"` // client-side default timeout
}

// Default returns a usable local configuration.
func Default() Config {
	return Config{
		Listen:           "127.0.0.1:1502",
		UnitIDs:          []uint8{1},
		RegisterCount:    128,
		DBPath:           ":memory:",
		LogPath:          "-",
		RequestTimeoutMs: 3000,
	}
}

// Load reads and validates a JSON config file.
func Load(path string) (Config, error) {
	cfg := Default()
	b, err := os.ReadFile(path)
	if err != nil {
		return cfg, fmt.Errorf("read config: %w", err)
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, fmt.Errorf("parse config %s: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return cfg, fmt.Errorf("invalid config %s: %w", path, err)
	}
	return cfg, nil
}

// Validate checks the configuration for consistency.
func (c Config) Validate() error {
	if c.Listen == "" {
		return fmt.Errorf("listen address is empty")
	}
	if len(c.UnitIDs) == 0 {
		return fmt.Errorf("unit_ids must not be empty")
	}
	seen := map[uint8]bool{}
	for _, u := range c.UnitIDs {
		if seen[u] {
			return fmt.Errorf("duplicate unit id %d", u)
		}
		seen[u] = true
	}
	if c.RegisterCount == 0 {
		return fmt.Errorf("register_count must be > 0")
	}
	if c.RequestTimeoutMs <= 0 {
		return fmt.Errorf("request_timeout_ms must be > 0")
	}
	return nil
}
