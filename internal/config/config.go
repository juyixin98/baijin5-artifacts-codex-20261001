// Package config defines the file format and validation for the proxy.
package config

import (
	"errors"
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"

	"sockswhitelist/internal/policy"
)

// Config is the full proxy configuration.
type Config struct {
	Listen   string        `yaml:"listen"`
	Database string        `yaml:"database"`
	Auth     Auth          `yaml:"auth"`
	Limits   Limits        `yaml:"limits"`
	Log      Log           `yaml:"log"`
	Rules    []policy.Rule `yaml:"rules"`
}

// Auth configures the negotiated method. Passwords are never read from this
// file; users are provisioned with the "adduser" subcommand into SQLite.
type Auth struct {
	Required bool `yaml:"required"`
}

// Limits holds every resource bound. All of them must be explicit so the
// server never silently runs unbounded.
type Limits struct {
	MaxConcurrentConnections int           `yaml:"max_concurrent_connections"`
	HandshakeTimeout         time.Duration `yaml:"handshake_timeout"`
	ResolveTimeout           time.Duration `yaml:"resolve_timeout"`
	DialTimeout              time.Duration `yaml:"dial_timeout"`
	IdleTimeout              time.Duration `yaml:"idle_timeout"`
	MaxBytesUp               int64         `yaml:"max_bytes_up"`
	MaxBytesDown             int64         `yaml:"max_bytes_down"`
}

// Log selects the explainability sink.
type Log struct {
	// Level: debug, info, warn, error.
	Level string `yaml:"level"`
	// TextFormat emits human lines; otherwise JSON lines.
	TextFormat bool `yaml:"text_format"`
}

// Default returns safe bounded defaults.
func Default() Config {
	return Config{
		Listen:   "127.0.0.1:1080",
		Database: "proxy.db",
		Auth:     Auth{Required: true},
		Limits: Limits{
			MaxConcurrentConnections: 64,
			HandshakeTimeout:         10 * time.Second,
			ResolveTimeout:           5 * time.Second,
			DialTimeout:              10 * time.Second,
			IdleTimeout:              2 * time.Minute,
			MaxBytesUp:               64 * 1024 * 1024,
			MaxBytesDown:             64 * 1024 * 1024,
		},
		Log: Log{Level: "info"},
	}
}

// Load reads, parses and validates a YAML config file, applying defaults for
// missing fields.
func Load(path string) (Config, error) {
	cfg := Default()
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("config: read %s: %w", path, err)
	}
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return Config{}, fmt.Errorf("config: parse %s: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

// Validate checks every bound and rule. Fail-fast with an exact field name.
func (c Config) Validate() error {
	if c.Listen == "" {
		return errors.New("config: listen must be set")
	}
	if c.Database == "" {
		return errors.New("config: database path must be set")
	}
	l := c.Limits
	if l.MaxConcurrentConnections <= 0 {
		return errors.New("config: limits.max_concurrent_connections must be > 0")
	}
	for _, pair := range []struct {
		name  string
		value time.Duration
	}{
		{"handshake_timeout", l.HandshakeTimeout},
		{"resolve_timeout", l.ResolveTimeout},
		{"dial_timeout", l.DialTimeout},
		{"idle_timeout", l.IdleTimeout},
	} {
		if pair.value <= 0 {
			return fmt.Errorf("config: limits.%s must be > 0", pair.name)
		}
	}
	if l.MaxBytesUp <= 0 || l.MaxBytesDown <= 0 {
		return errors.New("config: byte budgets must be > 0")
	}
	switch c.Log.Level {
	case "", "debug", "info", "warn", "error":
	default:
		return fmt.Errorf("config: unknown log level %q", c.Log.Level)
	}
	for i, r := range c.Rules {
		if _, err := policy.ValidateRule(r); err != nil {
			return fmt.Errorf("config: rules[%d]: %w", i, err)
		}
	}
	return nil
}

// Duration wrappers exist so YAML shows "10s"; kept here for docs tools.
var _ = time.Second
