// Package config loads and validates the smtpsink JSON configuration.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// Config is the complete runtime configuration.
type Config struct {
	Listen          string   `json:"listen"`            // host:port to bind
	Hostname        string   `json:"hostname"`          // greeting / EHLO identity
	LocalDomains    []string `json:"local_domains"`     // domains accepted at RCPT
	MaxLineBytes    int      `json:"max_line_bytes"`    // command line cap
	MaxMessageBytes int64    `json:"max_message_bytes"` // DATA body cap after un-stuffing
	MaxRecipients   int      `json:"max_recipients"`    // per-transaction RCPT cap
	MaxSessions     int      `json:"max_sessions"`      // concurrent connection cap
	StorageDir      string   `json:"storage_dir"`       // SQLite + .eml spool root
}

// Default returns a configuration suitable for local testing.
func Default() Config {
	return Config{
		Listen:          "127.0.0.1:2525",
		Hostname:        "smtpsink.local",
		LocalDomains:    []string{"example.test"},
		MaxLineBytes:    1000,
		MaxMessageBytes: 1 << 20, // 1 MiB
		MaxRecipients:   100,
		MaxSessions:     32,
		StorageDir:      "./data",
	}
}

// Load reads path and merges it over Default, then validates.
func Load(path string) (Config, error) {
	cfg := Default()
	b, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("config: read %s: %w", path, err)
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return Config{}, fmt.Errorf("config: parse %s: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

// Validate rejects configurations that cannot run safely.
func (c Config) Validate() error {
	if c.Listen == "" {
		return fmt.Errorf("config: listen must not be empty")
	}
	if c.Hostname == "" {
		return fmt.Errorf("config: hostname must not be empty")
	}
	if len(c.LocalDomains) == 0 {
		return fmt.Errorf("config: local_domains must name at least one test domain")
	}
	for _, d := range c.LocalDomains {
		if strings.TrimSpace(d) == "" || !strings.Contains(d, ".") {
			return fmt.Errorf("config: local_domains entry %q is not a domain", d)
		}
	}
	if c.MaxLineBytes < 512 {
		return fmt.Errorf("config: max_line_bytes must be >= 512 (RFC 5321 minimum)")
	}
	if c.MaxMessageBytes <= 0 {
		return fmt.Errorf("config: max_message_bytes must be positive")
	}
	if c.MaxRecipients <= 0 {
		return fmt.Errorf("config: max_recipients must be positive")
	}
	if c.MaxSessions <= 0 {
		return fmt.Errorf("config: max_sessions must be positive")
	}
	if c.StorageDir == "" {
		return fmt.Errorf("config: storage_dir must not be empty")
	}
	return nil
}
