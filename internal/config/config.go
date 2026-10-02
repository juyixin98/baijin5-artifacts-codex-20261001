// Package config holds startup configuration for the CoAP subset service.
// Values are loaded from a YAML-ish flat config file (simple key=value
// parser, no third-party YAML dep) with environment overrides.
package config

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the validated startup configuration.
type Config struct {
	// Network
	ListenNetwork string // "udp"
	ListenAddr    string // e.g. "127.0.0.1:5683"
	DBPath        string // SQLite file, or ":memory:" for tests

	// Reliable-message constants (RFC 7252 §4.8, Table).
	ACKTimeout       time.Duration // ACK_TIMEOUT default 2s; tests shrink it
	ACKRandomFactor  float64       // 1.0..1.5 (tests set 1.0 deterministic)
	MaxRetransmit    int           // MAX_RETRANSMIT default 4
	ExchangeLifetime time.Duration // derived; explicit in config too
	// Block1 partial-body retention (RFC 7959 §2.5): how long the server
	// keeps an unfinished Block1 upload before discarding it -> 4.08.
	Block1Retention time.Duration

	// Protocol subset
	MaxMessageSize int // hard cap on accepted datagram size (IP MTU-ish)
	MaxBodyBytes   int // max assembled resource body
	PreferredSZX   int // server-side preferred block exponent (0..6)
	TokenLength    int // client token bytes 1..8

	SeedResources []SeedResource // local synthetic fixtures
}

// SeedResource is a synthetic resource loaded at startup (no real data).
type SeedResource struct {
	Path          string
	ContentFormat uint16
	Body          []byte
	ETag          []byte
}

// Default returns a local-development configuration with RFC defaults.
func Default() *Config {
	c := &Config{
		ListenNetwork:    "udp",
		ListenAddr:       "127.0.0.1:0",
		DBPath:           "coaplab.db",
		ACKTimeout:       2 * time.Second,
		ACKRandomFactor:  1.0,
		MaxRetransmit:    4,
		ExchangeLifetime: 247 * time.Second,
		// ACK_TIMEOUT * ((2**MAX_RETRANSMIT)-1) * ACK_RANDOM_FACTOR + ...
		// Default RFC value 247s is huge for local use; tests override.
		Block1Retention: 30 * time.Second,
		MaxMessageSize:  1280,
		MaxBodyBytes:    1 << 20, // 1 MiB cap
		PreferredSZX:    6,       // 1024
		TokenLength:     4,
	}
	return c
}

// Load reads key=value lines (# comments, blank lines ignored).
func Load(path string) (*Config, error) {
	c := Default()
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		i := strings.IndexByte(line, '=')
		if i < 0 {
			return nil, fmt.Errorf("config: malformed line %q", line)
		}
		key := strings.TrimSpace(line[:i])
		val := strings.TrimSpace(line[i+1:])
		if err := set(c, key, val); err != nil {
			return nil, err
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return c, nil
}

// EnvOverride applies COAPLAB_* environment overrides.
func (c *Config) EnvOverride() {
	if v := os.Getenv("COAPLAB_LISTEN"); v != "" {
		c.ListenAddr = v
	}
	if v := os.Getenv("COAPLAB_DB"); v != "" {
		c.DBPath = v
	}
	if v := os.Getenv("COAPLAB_ACK_TIMEOUT"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			c.ACKTimeout = d
		}
	}
}

func set(c *Config, key, val string) error {
	switch key {
	case "listen":
		c.ListenAddr = val
	case "db_path":
		c.DBPath = val
	case "ack_timeout":
		d, err := time.ParseDuration(val)
		if err != nil {
			return fmt.Errorf("config: bad ack_timeout %q", val)
		}
		c.ACKTimeout = d
	case "ack_random_factor":
		f, err := strconv.ParseFloat(val, 64)
		if err != nil {
			return fmt.Errorf("config: bad ack_random_factor %q", val)
		}
		c.ACKRandomFactor = f
	case "max_retransmit":
		n, err := strconv.Atoi(val)
		if err != nil {
			return fmt.Errorf("config: bad max_retransmit %q", val)
		}
		c.MaxRetransmit = n
	case "exchange_lifetime":
		d, err := time.ParseDuration(val)
		if err != nil {
			return fmt.Errorf("config: bad exchange_lifetime %q", val)
		}
		c.ExchangeLifetime = d
	case "block1_retention":
		d, err := time.ParseDuration(val)
		if err != nil {
			return fmt.Errorf("config: bad block1_retention %q", val)
		}
		c.Block1Retention = d
	case "max_message_size":
		n, err := strconv.Atoi(val)
		if err != nil {
			return fmt.Errorf("config: bad max_message_size %q", val)
		}
		c.MaxMessageSize = n
	case "max_body_bytes":
		n, err := strconv.Atoi(val)
		if err != nil {
			return fmt.Errorf("config: bad max_body_bytes %q", val)
		}
		c.MaxBodyBytes = n
	case "preferred_szx":
		n, err := strconv.Atoi(val)
		if err != nil {
			return fmt.Errorf("config: bad preferred_szx %q", val)
		}
		c.PreferredSZX = n
	case "token_length":
		n, err := strconv.Atoi(val)
		if err != nil {
			return fmt.Errorf("config: bad token_length %q", val)
		}
		c.TokenLength = n
	default:
		return fmt.Errorf("config: unknown key %q", key)
	}
	return nil
}

// Validate enforces RFC and resource bounds.
func (c *Config) Validate() error {
	if c.ACKTimeout <= 0 {
		return fmt.Errorf("config: ack_timeout must be positive")
	}
	if c.ACKRandomFactor < 1.0 {
		return fmt.Errorf("config: ack_random_factor MUST be >= 1.0 (RFC 7252 §4.8)")
	}
	if c.MaxRetransmit < 0 || c.MaxRetransmit > 10 {
		return fmt.Errorf("config: max_retransmit out of range")
	}
	if c.PreferredSZX < 0 || c.PreferredSZX > 6 {
		return fmt.Errorf("config: preferred_szx must be 0..6")
	}
	if c.TokenLength < 1 || c.TokenLength > 8 {
		return fmt.Errorf("config: token_length must be 1..8")
	}
	if c.MaxMessageSize < 64 || c.MaxMessageSize > 65535 {
		return fmt.Errorf("config: max_message_size must be 64..65535")
	}
	if c.MaxBodyBytes < 256 {
		return fmt.Errorf("config: max_body_bytes too small")
	}
	if c.Block1Retention <= 0 {
		return fmt.Errorf("config: block1_retention must be positive")
	}
	return nil
}
