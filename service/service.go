// Package service exposes the pattern-match compiler over HTTP with
// request-scoped logging, categorized errors and module version reporting.
package service

import (
	"encoding/json"
	"os"
)

// Version is the service version reported in responses and logs.
const Version = "0.1.0"

// Error categories produced by the service layer itself. Lower layers
// contribute their own categories (parse_error, semantic_error,
// invalid_value, no_match, guard_error).
const (
	CatBadRequest = "bad_request"
	CatInternal   = "internal_error"
)

// Config tunes the server. Missing fields fall back to defaults.
type Config struct {
	Addr             string `json:"addr"`
	MaxBodyBytes     int64  `json:"max_body_bytes"`
	DiffDefaultCases int    `json:"diff_default_cases"`
	DiffMaxDepth     int    `json:"diff_max_depth"`
	EnumDepth        int    `json:"diff_enum_depth"`
	EnumLimit        int    `json:"diff_enum_limit"`
}

// Default returns the built-in configuration.
func Default() Config {
	return Config{
		Addr:             "127.0.0.1:8080",
		MaxBodyBytes:     1 << 20,
		DiffDefaultCases: 300,
		DiffMaxDepth:     4,
		EnumDepth:        3,
		EnumLimit:        2000,
	}
}

// Load reads a JSON config file over the defaults. An empty path yields
// the defaults.
func Load(path string) (Config, error) {
	cfg := Default()
	if path == "" {
		return cfg, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, err
	}
	return cfg, nil
}
