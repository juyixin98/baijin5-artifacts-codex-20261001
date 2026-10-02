// Package config defines and validates the local Modbus fixture's
// configuration. It contains only stdlib code and knows nothing about
// sockets or protocol handling; the fixture/server packages consume it.
package config

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net"
	"os"
)

// Version is the fixture configuration schema version. It is logged and
// exposed on the control plane so a run's exact processing location is
// explainable.
const Version = "modbus-fixture-1.0.0"

// Latency profiles select the fixture's synthetic per-request scheduling.
const (
	// LatencyNone: workers answer as soon as the engine returns.
	LatencyNone = "none"
	// LatencyTxnJitter: a deterministic delay of
	// (transaction_id % JitterBuckets) * JitterSlot, used to prove that a
	// single connection may carry responses out of send order while their
	// transaction/unit identity stays correct.
	LatencyTxnJitter = "txn_jitter"
)

// UnitConfig binds one unit id to one holding-register bank.
type UnitConfig struct {
	// UnitID is the MBAP unit identifier, 0..255.
	UnitID byte `json:"unit_id"`
	// RegisterCount is the holding register bank size (addresses 0..n-1).
	RegisterCount int `json:"register_count"`
	// InitialValues optionally presets registers from address 0. Missing
	// trailing registers stay zero.
	InitialValues []uint16 `json:"initial_values,omitempty"`
}

// Config is the full fixture configuration.
type Config struct {
	// Listen is the Modbus TCP listener address, e.g. "127.0.0.1:5020".
	// Port 0 requests an ephemeral port (the resolved address is reported
	// on startup; used by the black-box tests).
	Listen string `json:"listen"`
	// ControlListen is the loopback-only HTTP control plane address.
	ControlListen string `json:"control_listen"`
	// SQLiteDB is the audit database path. ":memory:" is rejected for the
	// server because audit rows must survive for inspection; tests use a
	// temp file path instead.
	SQLiteDB string `json:"sqlite_db"`
	// Workers is the per-connection request handler count. Requests on one
	// connection are dispatched to workers and a later request may be
	// answered first; replies are correlated by transaction id.
	Workers int `json:"workers"`
	// Latency selects the synthetic scheduling profile (none|txn_jitter).
	Latency string `json:"latency"`
	// JitterSlotMS is one jitter bucket in milliseconds (default 2).
	JitterSlotMS int `json:"jitter_slot_ms"`
	// Units is the unit-id to register-bank binding table.
	Units []UnitConfig `json:"units"`
}

// Default returns the baseline configuration: loopback-only endpoints, a
// 125-register FC03-max bank on unit 1, four workers, no jitter.
func Default() Config {
	return Config{
		Listen:        "127.0.0.1:5020",
		ControlListen: "127.0.0.1:15020",
		SQLiteDB:      "modbus_fixture.db",
		Workers:       4,
		Latency:       LatencyNone,
		JitterSlotMS:  2,
		Units: []UnitConfig{
			{UnitID: 0x01, RegisterCount: 125},
		},
	}
}

// Load reads, parses and validates a JSON configuration file. Unknown
// fields are rejected so a mistyped key fails loudly instead of being
// silently ignored.
func Load(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read config %q: %w", path, err)
	}
	cfg := Default()
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&cfg); err != nil {
		return Config{}, fmt.Errorf("parse config %q: %w", path, err)
	}
	if err := cfg.Validate(); err != nil {
		return Config{}, fmt.Errorf("validate config %q: %w", path, err)
	}
	return cfg, nil
}

// Validate checks cross-field invariants independently of file loading.
func (c Config) Validate() error {
	if c.Listen == "" {
		return fmt.Errorf("listen address is required")
	}
	if err := checkLoopback("listen", c.Listen); err != nil {
		return err
	}
	if c.ControlListen == "" {
		return fmt.Errorf("control_listen address is required")
	}
	if err := checkLoopback("control_listen", c.ControlListen); err != nil {
		return err
	}
	if c.SQLiteDB == "" {
		return fmt.Errorf("sqlite_db path is required")
	}
	if c.SQLiteDB == ":memory:" {
		return fmt.Errorf(`sqlite_db ":memory:" is not allowed for the server`)
	}
	if c.Workers < 1 || c.Workers > 256 {
		return fmt.Errorf("workers must be in [1,256], got %d", c.Workers)
	}
	switch c.Latency {
	case LatencyNone, LatencyTxnJitter:
	default:
		return fmt.Errorf("latency must be %q or %q, got %q",
			LatencyNone, LatencyTxnJitter, c.Latency)
	}
	if c.JitterSlotMS < 0 || c.JitterSlotMS > 1000 {
		return fmt.Errorf("jitter_slot_ms must be in [0,1000], got %d",
			c.JitterSlotMS)
	}
	if len(c.Units) == 0 {
		return fmt.Errorf("at least one unit binding is required")
	}
	seen := make(map[byte]struct{}, len(c.Units))
	for i, u := range c.Units {
		if u.RegisterCount < 1 || u.RegisterCount > 0x10000 {
			return fmt.Errorf("units[%d]: register_count must be in [1,65536], got %d",
				i, u.RegisterCount)
		}
		if len(u.InitialValues) > u.RegisterCount {
			return fmt.Errorf("units[%d]: %d initial values exceed register count %d",
				i, len(u.InitialValues), u.RegisterCount)
		}
		if _, dup := seen[u.UnitID]; dup {
			return fmt.Errorf("units[%d]: duplicate unit id 0x%02X", i, u.UnitID)
		}
		seen[u.UnitID] = struct{}{}
	}
	return nil
}

// checkLoopback ensures a TCP address parses and binds to loopback: the
// fixture is synthetic and local, it must never listen on 0.0.0.0.
func checkLoopback(field, addr string) error {
	tcp, err := net.ResolveTCPAddr("tcp", addr)
	if err != nil {
		return fmt.Errorf("%s: invalid address %q: %w", field, addr, err)
	}
	if !tcp.IP.IsLoopback() {
		return fmt.Errorf("%s: %q is not a loopback address; the fixture is local-only",
			field, addr)
	}
	return nil
}
