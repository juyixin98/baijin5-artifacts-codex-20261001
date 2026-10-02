// Package config loads runtime configuration from a JSON file and
// environment overrides. Configuration is independent of the codec so
// deployments can tighten the resource limits without touching code.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"berconf/internal/ber"
)

// Config is the complete service configuration.
type Config struct {
	Service ServiceConfig `json:"service"`
	HTTP    HTTPConfig    `json:"http"`
	Storage StorageConfig `json:"storage"`
	Logging LoggingConfig `json:"logging"`
	Limits  ber.Limits    `json:"limits"`
}

type ServiceConfig struct {
	Name        string `json:"name"`
	Environment string `json:"environment"`
	Version     string `json:"version"`
}

type HTTPConfig struct {
	ListenAddr        string `json:"listen_addr"`
	ReadTimeoutMS     int    `json:"read_timeout_ms"`
	WriteTimeoutMS    int    `json:"write_timeout_ms"`
	MaxBodyBytes      int64  `json:"max_body_bytes"`
	ShutdownTimeoutMS int    `json:"shutdown_timeout_ms"`
}

type StorageConfig struct {
	// Driver: "modernc-sqlite" (pure Go, default) or "mattn-sqlite3" (cgo).
	Driver          string `json:"driver"`
	DSN             string `json:"dsn"`
	MaxOpenConns    int    `json:"max_open_conns"`
	RequestLogTable string `json:"request_log_table"`
}

type LoggingConfig struct {
	Level      string `json:"level"`       // debug | info | warn | error
	Format     string `json:"format"`      // json | text
	OutputPath string `json:"output_path"` // stdout | stderr | file path
}

// Default returns the built-in configuration used when no file is given.
func Default() Config {
	return Config{
		Service: ServiceConfig{
			Name:        "ber-restricted-codec",
			Environment: "local",
			Version:     "1.0.0",
		},
		HTTP: HTTPConfig{
			ListenAddr:        "127.0.0.1:8480",
			ReadTimeoutMS:     5000,
			WriteTimeoutMS:    5000,
			MaxBodyBytes:      1 << 20,
			ShutdownTimeoutMS: 5000,
		},
		Storage: StorageConfig{
			Driver:          "modernc-sqlite",
			DSN:             "file:data/berserv.db?cache=shared&_pragma=busy_timeout(5000)",
			MaxOpenConns:    2,
			RequestLogTable: "request_log",
		},
		Logging: LoggingConfig{
			Level:      "info",
			Format:     "json",
			OutputPath: "stdout",
		},
		Limits: ber.DefaultLimits(),
	}
}

// Load reads the optional JSON file and applies BERSERV_* environment
// overrides. Missing path is not an error: defaults are used.
func Load(path string) (Config, error) {
	cfg := Default()
	if path != "" {
		raw, err := os.ReadFile(path)
		if err != nil {
			return cfg, fmt.Errorf("read config %s: %w", path, err)
		}
		if err := json.Unmarshal(raw, &cfg); err != nil {
			return cfg, fmt.Errorf("parse config %s: %w", path, err)
		}
	}
	if err := cfg.applyEnv(); err != nil {
		return cfg, err
	}
	if err := cfg.validate(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func (c *Config) applyEnv() error {
	envStr := map[string]*string{
		"BERSERV_LISTEN_ADDR": &c.HTTP.ListenAddr,
		"BERSERV_SQLITE_DSN":  &c.Storage.DSN,
		"BERSERV_LOG_LEVEL":   &c.Logging.Level,
		"BERSERV_LOG_FORMAT":  &c.Logging.Format,
		"BERSERV_ENV":         &c.Service.Environment,
	}
	for key, dst := range envStr {
		if v, ok := os.LookupEnv(key); ok {
			*dst = v
		}
	}
	envInt := map[string]*int{
		"BERSERV_MAX_DEPTH":         &c.Limits.MaxDepth,
		"BERSERV_MAX_TAG_BYTES":     &c.Limits.MaxTagBytes,
		"BERSERV_MAX_LENGTH_BYTES":  &c.Limits.MaxLengthBytes,
		"BERSERV_MAX_CONTENT_BYTES": &c.Limits.MaxContentBytes,
		"BERSERV_MAX_CHILDREN":      &c.Limits.MaxChildren,
		"BERSERV_MAX_INTEGER_BYTES": &c.Limits.MaxIntegerBytes,
	}
	for key, dst := range envInt {
		if v, ok := os.LookupEnv(key); ok {
			n, err := strconv.Atoi(v)
			if err != nil {
				return fmt.Errorf("env %s: %w", key, err)
			}
			*dst = n
		}
	}
	if v, ok := os.LookupEnv("BERSERV_MAX_BODY_BYTES"); ok {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil {
			return fmt.Errorf("env BERSERV_MAX_BODY_BYTES: %w", err)
		}
		c.HTTP.MaxBodyBytes = n
	}
	return nil
}

func (c *Config) validate() error {
	if !strings.Contains(c.HTTP.ListenAddr, ":") {
		return fmt.Errorf("http.listen_addr must be host:port")
	}
	if c.HTTP.MaxBodyBytes <= 0 {
		return fmt.Errorf("http.max_body_bytes must be positive")
	}
	switch c.Storage.Driver {
	case "modernc-sqlite", "mattn-sqlite3":
	default:
		return fmt.Errorf("storage.driver must be modernc-sqlite or mattn-sqlite3, got %q", c.Storage.Driver)
	}
	if c.Storage.RequestLogTable == "" {
		return fmt.Errorf("storage.request_log_table must not be empty")
	}
	if !isSafeIdent(c.Storage.RequestLogTable) {
		return fmt.Errorf("storage.request_log_table %q is not a safe identifier", c.Storage.RequestLogTable)
	}
	l := c.Limits.Normalize()
	c.Limits = l
	return nil
}

// isSafeIdent guards identifiers interpolated into DDL (the table name is
// config-controlled, never user input — but validate anyway).
func isSafeIdent(s string) bool {
	if s == "" || len(s) > 64 {
		return false
	}
	for i, r := range s {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r == '_':
		case i > 0 && r >= '0' && r <= '9':
		default:
			return false
		}
	}
	return true
}
