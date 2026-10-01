// Package config loads standalone, file-based configuration for smtpsink.
// Nothing is sourced from a real deployment environment; the defaults bind
// loopback only.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"strings"

	"smtpsink/internal/protocol"
)

// Config is the complete service configuration.
type Config struct {
	Listen       ListenConfig    `json:"listen"`
	Hostname     string          `json:"hostname"`
	Storage      StorageConfig   `json:"storage"`
	LocalDomains []string        `json:"local_domains"`
	Limits       protocol.Limits `json:"limits"`
	Timeouts     TimeoutConfig   `json:"timeouts"`
}

// ListenConfig configures the TCP listener. Host must resolve to loopback:
// this service never accepts mail from off-machine peers.
type ListenConfig struct {
	Host string `json:"host"`
	Port int    `json:"port"`
}

// StorageConfig selects the SQLite file and local quota.
type StorageConfig struct {
	Path          string `json:"path"`
	MaxTotalBytes int64  `json:"max_total_bytes"`
}

// TimeoutConfig caps how long a peer may stay idle or hold a connection.
type TimeoutConfig struct {
	// Idle is the deadline for a single command / DATA line read.
	IdleMS int `json:"idle_ms"`
	// Session caps total connection lifetime.
	SessionMS int `json:"session_ms"`
}

// Default returns safe loopback-only defaults.
func Default() Config {
	return Config{
		Listen:       ListenConfig{Host: "127.0.0.1", Port: 2525},
		Hostname:     "smtpsink.local",
		Storage:      StorageConfig{Path: "./smtpsink.db", MaxTotalBytes: 64 << 20},
		LocalDomains: []string{"localhost", "sink.local"},
		Limits: protocol.Limits{
			CommandLineBytes: 512,
			DataLineBytes:    1000,
			MessageBytes:     1 << 20,
			Recipients:       20,
			MessagesPerConn:  10,
			CommandsPerConn:  200,
		},
		Timeouts: TimeoutConfig{IdleMS: 60_000, SessionMS: 600_000},
	}
}

// Load reads JSON over defaults. A missing file yields defaults.
func Load(path string) (Config, error) {
	cfg := Default()
	if path == "" {
		return cfg, nil
	}
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return cfg, nil
	}
	if err != nil {
		return cfg, fmt.Errorf("config: read %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return cfg, fmt.Errorf("config: parse %s: %w", path, err)
	}
	return cfg, cfg.Validate()
}

// Validate enforces local-only binding and positive resource limits.
func (c Config) Validate() error {
	var problems []string

	host := c.Listen.Host
	if host == "" {
		problems = append(problems, "listen.host required")
	} else if !isLoopback(host) {
		problems = append(problems, "listen.host must be loopback (127.0.0.1/::1/localhost), got "+host)
	}
	if c.Listen.Port <= 0 || c.Listen.Port > 65535 {
		problems = append(problems, "listen.port must be 1..65535")
	}
	if strings.TrimSpace(c.Hostname) == "" {
		problems = append(problems, "hostname required")
	}
	if strings.TrimSpace(c.Storage.Path) == "" {
		problems = append(problems, "storage.path required")
	}
	if len(c.LocalDomains) == 0 {
		problems = append(problems, "at least one local domain required")
	}
	l := c.Limits
	if l.CommandLineBytes <= 0 || l.DataLineBytes <= 0 || l.MessageBytes <= 0 {
		problems = append(problems, "line/message byte limits must be positive")
	}
	if l.Recipients <= 0 || l.MessagesPerConn <= 0 || l.CommandsPerConn <= 0 {
		problems = append(problems, "recipient/message/command counts must be positive")
	}
	if c.Timeouts.IdleMS <= 0 || c.Timeouts.SessionMS <= 0 {
		problems = append(problems, "timeouts must be positive")
	}
	if len(problems) > 0 {
		return fmt.Errorf("config invalid: %s", strings.Join(problems, "; "))
	}
	return nil
}

func isLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// Address renders the listen endpoint.
func (c Config) Address() string {
	return net.JoinHostPort(c.Listen.Host, fmt.Sprintf("%d", c.Listen.Port))
}
