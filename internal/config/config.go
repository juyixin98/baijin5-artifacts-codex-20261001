// Package config defines the service configuration layer: file loading,
// defaults and validation, kept independent of protocol and transport code.
package config

import (
	"encoding/json"
	"fmt"
	"os"

	"h2svc/internal/frame"
)

// Config is the root service configuration.
type Config struct {
	// ListenAddr is the TCP address the server binds, e.g. "127.0.0.1:8443".
	ListenAddr string `json:"listen_addr"`
	// MaxFrameSize is the inbound frame payload bound (advertised too).
	MaxFrameSize uint32 `json:"max_frame_size"`
	// InitialRecvWindow is the per-stream receive window we advertise.
	InitialRecvWindow int64 `json:"initial_recv_window"`
	// SendQueueCapacity bounds the per-connection outbound frame queue.
	SendQueueCapacity int `json:"send_queue_capacity"`
	// MaxPendingBodyBytes bounds per-stream buffered response bytes.
	MaxPendingBodyBytes int `json:"max_pending_body_bytes"`
	// LargeBodyBytes is the body size served by the predefined GET /large route.
	LargeBodyBytes int `json:"large_body_bytes"`
	// ReadTimeoutMs bounds a single frame read; 0 disables.
	ReadTimeoutMs int `json:"read_timeout_ms"`
	// WriteTimeoutMs bounds a single frame write; 0 disables.
	WriteTimeoutMs int `json:"write_timeout_ms"`
	// JournalPath is the SQLite diagnostics database path (":memory:" allowed).
	JournalPath string `json:"journal_path"`
}

// Default returns a fully usable configuration.
func Default() Config {
	return Config{
		ListenAddr:          "127.0.0.1:8080",
		MaxFrameSize:        frame.DefaultMaxFrameSize,
		InitialRecvWindow:   65535,
		SendQueueCapacity:   64,
		MaxPendingBodyBytes: 1 << 20,
		LargeBodyBytes:      200000,
		ReadTimeoutMs:       30000,
		WriteTimeoutMs:      10000,
		JournalPath:         "h2svc-journal.db",
	}
}

// Load reads a JSON config file, layering it on top of Default().
func Load(path string) (Config, error) {
	cfg := Default()
	b, err := os.ReadFile(path)
	if err != nil {
		return cfg, fmt.Errorf("config: read %s: %w", path, err)
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, fmt.Errorf("config: parse %s: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

// Validate rejects inconsistent or out-of-range settings explicitly.
func (c Config) Validate() error {
	if c.ListenAddr == "" {
		return fmt.Errorf("config: listen_addr must not be empty")
	}
	if c.MaxFrameSize < frame.DefaultMaxFrameSize || c.MaxFrameSize > frame.MaxAllowedFrameSize {
		return fmt.Errorf("config: max_frame_size %d out of range [%d, %d]",
			c.MaxFrameSize, frame.DefaultMaxFrameSize, frame.MaxAllowedFrameSize)
	}
	if c.InitialRecvWindow <= 0 || c.InitialRecvWindow > 0x7fffffff {
		return fmt.Errorf("config: initial_recv_window %d out of range (0, 2^31-1]", c.InitialRecvWindow)
	}
	if c.SendQueueCapacity <= 0 {
		return fmt.Errorf("config: send_queue_capacity must be positive, got %d", c.SendQueueCapacity)
	}
	if c.MaxPendingBodyBytes <= 0 {
		return fmt.Errorf("config: max_pending_body_bytes must be positive, got %d", c.MaxPendingBodyBytes)
	}
	if c.LargeBodyBytes <= 0 {
		return fmt.Errorf("config: large_body_bytes must be positive, got %d", c.LargeBodyBytes)
	}
	if c.JournalPath == "" {
		return fmt.Errorf("config: journal_path must not be empty")
	}
	return nil
}
