// Package config loads the local JSON configuration used by the verifier and
// the verification script. Only the Go standard library is used.
package config

import (
	"encoding/json"
	"fmt"
	"os"
)

// ScenarioPair points at the old and new snapshots of one change scenario.
type ScenarioPair struct {
	Old       string `json:"old"`
	New       string `json:"new"`
	OldSemVer string `json:"old_semver"`
	NewSemVer string `json:"new_semver"`
}

// Config is the verifier configuration.
type Config struct {
	FingerprintSchema string                  `json:"fingerprint_schema"`
	CompileSemVer     string                  `json:"compile_semver"`
	Entrypoint        string                  `json:"entrypoint"`
	CallDepthLimit    int                     `json:"call_depth_limit"`
	Scenarios         map[string]ScenarioPair `json:"scenarios"`
	ErrorFixtures     map[string]string       `json:"error_fixtures"`
	SensitiveFixture  string                  `json:"sensitive_fixture"`
}

// Load reads and validates a JSON config file.
func Load(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("parse config %s: %w", path, err)
	}
	if c.FingerprintSchema == "" {
		return nil, fmt.Errorf("config: fingerprint_schema is required")
	}
	if c.CompileSemVer == "" {
		return nil, fmt.Errorf("config: compile_semver is required")
	}
	if c.CallDepthLimit <= 0 {
		c.CallDepthLimit = 256
	}
	return &c, nil
}
