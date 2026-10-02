// Package config defines the service configuration loaded from JSON.
//
// JSON is used rather than YAML to keep the build on the Go standard
// library plus modernc.org/sqlite; the structure mirrors the documented
// config file field-for-field.
package config

import (
	"encoding/json"
	"fmt"
	"os"
)

// Config is the hpackd configuration.
type Config struct {
	Server  ServerConfig  `json:"server"`
	Hpack   HpackConfig   `json:"hpack"`
	Storage StorageConfig `json:"storage"`
	Logging LoggingConfig `json:"logging"`
}

// ServerConfig configures the listener and TLS material.
type ServerConfig struct {
	// Listen is the host:port for the h2c/h2 listener.
	Listen string `json:"listen"`
	// CertFile/KeyFile enable TLS (h2). Empty pair selects h2c on Listen.
	CertFile string `json:"cert_file"`
	KeyFile  string `json:"key_file"`
	// MaxConcurrentStreams advertises SETTINGS_MAX_CONCURRENT_STREAMS.
	MaxConcurrentStreams uint32 `json:"max_concurrent_streams"`
}

// HpackConfig mirrors the SETTINGS_HEADER_TABLE_SIZE and local budgets.
type HpackConfig struct {
	// TableSize is SETTINGS_HEADER_TABLE_SIZE advertised to peers.
	TableSize uint32 `json:"table_size"`
	// MaxStringLen caps one decompressed name/value.
	MaxStringLen int `json:"max_string_len"`
	// MaxHeaderBlockEmitted caps total name+value bytes per block.
	MaxHeaderBlockEmitted uint64 `json:"max_header_block_emitted"`
	// MaxHeaderFields caps the number of fields in one block.
	MaxHeaderFields int `json:"max_header_fields"`
	// Huffman enables Huffman on responses the server encodes.
	Huffman bool `json:"huffman"`
}

// StorageConfig selects the SQLite database location.
type StorageConfig struct {
	SQLitePath string `json:"sqlite_path"`
}

// LoggingConfig controls request-correlated structured logs.
type LoggingConfig struct {
	// Level: debug | info | error.
	Level string `json:"level"`
	// EchoHeaders includes decoded header names in logs (sensitive values
	// are always redacted).
	EchoHeaders bool `json:"echo_headers"`
}

// Default returns built-in defaults.
func Default() Config {
	return Config{
		Server: ServerConfig{
			Listen:               "127.0.0.1:8443",
			MaxConcurrentStreams: 100,
		},
		Hpack: HpackConfig{
			TableSize:             4096,
			MaxStringLen:          64 * 1024,
			MaxHeaderBlockEmitted: 1 << 20,
			MaxHeaderFields:       256,
			Huffman:               true,
		},
		Storage: StorageConfig{SQLitePath: "hpackd.db"},
		Logging: LoggingConfig{Level: "info"},
	}
}

// Load reads JSON at path over Defaults.
func Load(path string) (Config, error) {
	cfg := Default()
	raw, err := os.ReadFile(path)
	if err != nil {
		return cfg, fmt.Errorf("read config %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return cfg, fmt.Errorf("parse config %s: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

// Validate checks configuration invariants.
func (c Config) Validate() error {
	if c.Server.Listen == "" {
		return fmt.Errorf("server.listen must be set")
	}
	if c.Hpack.TableSize == 0 {
		return fmt.Errorf("hpack.table_size must be > 0")
	}
	if (c.Server.CertFile == "") != (c.Server.KeyFile == "") {
		return fmt.Errorf("cert_file and key_file must be set together")
	}
	return nil
}
