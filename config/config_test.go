package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTemp(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "config.json")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	return p
}

func TestLoadValidFullConfig(t *testing.T) {
	p := writeTemp(t, `{
		"listen": "127.0.0.1:5020",
		"control_listen": "127.0.0.1:15020",
		"sqlite_db": "test.db",
		"workers": 8,
		"latency": "txn_jitter",
		"jitter_slot_ms": 3,
		"units": [
			{"unit_id": 1, "register_count": 125, "initial_values": [1, 2, 3]},
			{"unit_id": 255, "register_count": 10}
		]
	}`)
	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Workers != 8 || cfg.Latency != LatencyTxnJitter {
		t.Fatalf("unexpected cfg: %+v", cfg)
	}
	if len(cfg.Units) != 2 || cfg.Units[1].UnitID != 0xFF {
		t.Fatalf("units: %+v", cfg.Units)
	}
}

func TestRejectUnknownField(t *testing.T) {
	p := writeTemp(t, `{
		"listen": "127.0.0.1:5020",
		"control_listen": "127.0.0.1:15020",
		"sqlite_db": "x.db", "workers": 2, "latency": "none",
		"units": [{"unit_id": 1, "register_count": 5}],
		"prototcol": "modbus"
	}`)
	if _, err := Load(p); err == nil ||
		!strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("want unknown field error, got %v", err)
	}
}

func TestRejectNonLoopback(t *testing.T) {
	c := Default()
	c.Listen = "0.0.0.0:5020"
	err := c.Validate()
	if err == nil || !strings.Contains(err.Error(), "loopback") {
		t.Fatalf("want loopback rejection, got %v", err)
	}
}

func TestRejectMemoryDBAndBadRanges(t *testing.T) {
	c := Default()
	c.SQLiteDB = ":memory:"
	if err := c.Validate(); err == nil {
		t.Fatal("memory db should be rejected")
	}

	for _, bad := range []func(*Config){
		func(c *Config) { c.Workers = 0 },
		func(c *Config) { c.Latency = "random" },
		func(c *Config) { c.Units = nil },
		func(c *Config) { c.Units[0].RegisterCount = 0 },
		func(c *Config) { c.Units = append(c.Units, c.Units[0]) /* dup */ },
		func(c *Config) {
			c.Units[0].RegisterCount = 3
			c.Units[0].InitialValues = []uint16{1, 2, 3, 4, 5, 6}
		},
	} {
		cc := Default()
		bad(&cc)
		if err := cc.Validate(); err == nil {
			t.Fatalf("invalid config accepted: %+v", cc)
		}
	}
}
