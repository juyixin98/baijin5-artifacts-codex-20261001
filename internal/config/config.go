// Package config defines the file-based configuration of the SOCKS5 proxy
// and validates it at the system boundary. All values that drive the
// security policy (timeouts, budgets, rules) are checked here before use.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"time"
)

// Duration is a time.Duration that can be expressed as "10s" / "500ms"
// or as a plain integer number of nanoseconds in JSON.
type Duration struct {
	time.Duration
}

func (d Duration) MarshalJSON() ([]byte, error) {
	return json.Marshal(d.Duration.String())
}

func (d *Duration) UnmarshalJSON(b []byte) error {
	if len(b) > 0 && b[0] == '"' {
		var s string
		if err := json.Unmarshal(b, &s); err != nil {
			return err
		}
		parsed, err := time.ParseDuration(s)
		if err != nil {
			return fmt.Errorf("invalid duration %q: %w", s, err)
		}
		d.Duration = parsed
		return nil
	}
	var ns int64
	if err := json.Unmarshal(b, &ns); err != nil {
		return fmt.Errorf("duration must be a string or nanosecond integer: %w", err)
	}
	if ns < 0 {
		return errors.New("duration must not be negative")
	}
	d.Duration = time.Duration(ns)
	return nil
}

// Rule is one whitelist entry.
//
//	kind "cidr":   Value is a CIDR prefix, e.g. "127.0.0.0/8" or "::1/128".
//	kind "domain": Value is a DNS name; Mode is "exact" (default) or "suffix".
type Rule struct {
	Kind  string `json:"kind"`
	Value string `json:"value"`
	Mode  string `json:"mode,omitempty"`
	Note  string `json:"note,omitempty"`
}

// DatabaseConfig configures the SQLite store that holds rules and the audit log.
type DatabaseConfig struct {
	Path string `json:"path"`
}

// ResolverConfig configures name resolution. Hosts is an /etc/hosts-style
// static table used for local, fully offline targets; names absent from it
// are resolved with the platform resolver.
type ResolverConfig struct {
	Hosts map[string][]string `json:"hosts,omitempty"`
}

// Config is the validated proxy configuration.
type Config struct {
	Listen           string         `json:"listen"`
	HandshakeTimeout Duration       `json:"handshake_timeout"`
	DialTimeout      Duration       `json:"dial_timeout"`
	IdleTimeout      Duration       `json:"idle_timeout"`
	ShutdownTimeout  Duration       `json:"shutdown_timeout"`
	MaxConnections   int            `json:"max_connections"`
	ByteBudget       int64          `json:"byte_budget_per_connection"`
	Database         DatabaseConfig `json:"database"`
	Resolver         ResolverConfig `json:"resolver"`
	Rules            []Rule         `json:"rules"`
}

// Default returns a safe baseline: loopback-only listener, local whitelist.
func Default() Config {
	return Config{
		Listen:           "127.0.0.1:1080",
		HandshakeTimeout: Duration{10 * time.Second},
		DialTimeout:      Duration{10 * time.Second},
		IdleTimeout:      Duration{0},
		ShutdownTimeout:  Duration{5 * time.Second},
		MaxConnections:   64,
		ByteBudget:       4 * 1024 * 1024,
		Database:         DatabaseConfig{Path: "socks5d.db"},
	}
}

// Load reads, parses and validates a configuration file.
func Load(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read config %s: %w", path, err)
	}
	cfg := Default()
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return Config{}, fmt.Errorf("parse config %s: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return Config{}, fmt.Errorf("validate config %s: %w", path, err)
	}
	return cfg, nil
}

// Validate checks every boundary value and normalizes rule notation.
func (c *Config) Validate() error {
	var problems []string
	if c.Listen == "" {
		problems = append(problems, "listen must not be empty")
	}
	if c.HandshakeTimeout.Duration <= 0 {
		problems = append(problems, "handshake_timeout must be > 0")
	}
	if c.DialTimeout.Duration <= 0 {
		problems = append(problems, "dial_timeout must be > 0")
	}
	if c.IdleTimeout.Duration < 0 {
		problems = append(problems, "idle_timeout must be >= 0")
	}
	if c.ShutdownTimeout.Duration < 0 {
		problems = append(problems, "shutdown_timeout must be >= 0")
	}
	if c.MaxConnections < 1 {
		problems = append(problems, "max_connections must be >= 1")
	}
	if c.ByteBudget <= 0 {
		problems = append(problems, "byte_budget_per_connection must be > 0")
	}
	if c.Database.Path == "" {
		problems = append(problems, "database.path must not be empty")
	}
	for i := range c.Rules {
		if err := normalizeRule(&c.Rules[i]); err != nil {
			problems = append(problems, fmt.Sprintf("rules[%d]: %v", i, err))
		}
	}
	for name, addrs := range c.Resolver.Hosts {
		if !isDomainName(name) {
			problems = append(problems, fmt.Sprintf("resolver.hosts: invalid host name %q", name))
		}
		for _, a := range addrs {
			if _, err := netip.ParseAddr(a); err != nil {
				problems = append(problems, fmt.Sprintf("resolver.hosts[%s]: invalid IP %q", name, a))
			}
		}
	}
	if len(problems) > 0 {
		return errors.New(strings.Join(problems, "; "))
	}
	return nil
}

func normalizeRule(r *Rule) error {
	switch r.Kind {
	case "cidr":
		prefix, err := parseCIDRLenient(strings.TrimSpace(r.Value))
		if err != nil {
			return fmt.Errorf("invalid CIDR %q: %w", r.Value, err)
		}
		r.Value = prefix.Masked().String()
		r.Mode = ""
		return nil
	case "domain":
		r.Value = strings.ToLower(strings.TrimSpace(r.Value))
		if !isDomainName(r.Value) {
			return fmt.Errorf("invalid domain %q", r.Value)
		}
		if r.Mode == "" {
			r.Mode = "exact"
		}
		if r.Mode != "exact" && r.Mode != "suffix" {
			return fmt.Errorf("domain mode must be exact or suffix, got %q", r.Mode)
		}
		return nil
	default:
		return fmt.Errorf("unknown rule kind %q", r.Kind)
	}
}

// parseCIDRLenient parses an "addr/bits" prefix and tolerates host bits set,
// masking them off. netip.ParsePrefix itself rejects host bits, but accepting
// "127.0.0.1/8" and normalizing it to "127.0.0.0/8" is friendlier and
// unambiguous.
func parseCIDRLenient(s string) (netip.Prefix, error) {
	if prefix, err := netip.ParsePrefix(s); err == nil {
		return prefix, nil
	}
	host, bitsStr, err := splitCIDR(s)
	if err != nil {
		return netip.Prefix{}, fmt.Errorf("invalid CIDR %q: %w", s, err)
	}
	addr, err := netip.ParseAddr(host)
	if err != nil {
		return netip.Prefix{}, fmt.Errorf("invalid CIDR %q: bad address: %w", s, err)
	}
	bits, err := strconv.Atoi(bitsStr)
	if err != nil {
		return netip.Prefix{}, fmt.Errorf("invalid CIDR %q: bad prefix length: %w", s, err)
	}
	maxBits := 32
	if addr.Is6() {
		maxBits = 128
	}
	if bits < 0 || bits > maxBits {
		return netip.Prefix{}, fmt.Errorf("invalid CIDR %q: prefix length %d out of range", s, bits)
	}
	return netip.PrefixFrom(addr, bits), nil
}

func splitCIDR(s string) (string, string, error) {
	i := strings.LastIndex(s, "/")
	if i < 0 {
		return "", "", errors.New("missing prefix length")
	}
	return s[:i], s[i+1:], nil
}

// non-empty labels of 1..63 characters using letters, digits and hyphens,
// total length at most 253.
func isDomainName(name string) bool {
	name = strings.TrimSuffix(strings.ToLower(strings.TrimSpace(name)), ".")
	if name == "" || len(name) > 253 {
		return false
	}
	for _, label := range strings.Split(name, ".") {
		if len(label) == 0 || len(label) > 63 {
			return false
		}
		if label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, c := range label {
			switch {
			case c >= 'a' && c <= 'z', c >= '0' && c <= '9', c == '-':
			default:
				return false
			}
		}
	}
	return true
}
