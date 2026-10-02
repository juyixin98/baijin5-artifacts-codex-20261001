package config_test

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"coaplab/internal/config"
)

func writeConf(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "c.conf")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestLoad_OverridesAndDefaults(t *testing.T) {
	p := writeConf(t, `
# comment line
listen=127.0.0.1:9999
ack_timeout=50ms
ack_random_factor=1.25
max_retransmit=2
block1_retention=1s
preferred_szx=3
token_length=8
unknown_key=1
`)
	_, err := config.Load(p)
	if err == nil {
		t.Fatal("unknown key must be rejected")
	}

	p = writeConf(t, `
listen=127.0.0.1:9999
ack_timeout=50ms
ack_random_factor=1.25
max_retransmit=2
block1_retention=1s
preferred_szx=3
token_length=8
`)
	c, err := config.Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if c.ListenAddr != "127.0.0.1:9999" || c.ACKTimeout != 50*time.Millisecond ||
		c.ACKRandomFactor != 1.25 || c.MaxRetransmit != 2 ||
		c.Block1Retention != time.Second || c.PreferredSZX != 3 || c.TokenLength != 8 {
		t.Fatalf("values not parsed: %+v", c)
	}
}

func TestLoad_BadValuesAndFile(t *testing.T) {
	cases := map[string]string{
		"bad timeout":  "ack_timeout=not-a-duration\n",
		"bad float":    "ack_random_factor=x\n",
		"bad int":      "max_retransmit=x\n",
		"bad duration": "block1_retention=nope\n",
		"malformed":    "no-equals-sign\n",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := config.Load(writeConf(t, body)); err == nil {
				t.Fatal("expected error")
			}
		})
	}
	if _, err := config.Load(filepath.Join(t.TempDir(), "missing")); err == nil {
		t.Fatal("missing file must error")
	}
}

func TestValidate_Bounds(t *testing.T) {
	bad := []func(*config.Config){
		func(c *config.Config) { c.ACKTimeout = 0 },
		func(c *config.Config) { c.ACKRandomFactor = 0.9 },
		func(c *config.Config) { c.MaxRetransmit = -1 },
		func(c *config.Config) { c.PreferredSZX = 7 },
		func(c *config.Config) { c.TokenLength = 0 },
		func(c *config.Config) { c.TokenLength = 9 },
		func(c *config.Config) { c.MaxMessageSize = 10 },
		func(c *config.Config) { c.Block1Retention = 0 },
	}
	for i, mutate := range bad {
		c := config.Default()
		mutate(c)
		if err := c.Validate(); err == nil {
			t.Fatalf("case %d must be invalid", i)
		}
	}
	if err := config.Default().Validate(); err != nil {
		t.Fatalf("default must be valid: %v", err)
	}
}

func TestEnvOverride(t *testing.T) {
	c := config.Default()
	t.Setenv("COAPLAB_LISTEN", "127.0.0.1:1234")
	t.Setenv("COAPLAB_DB", "/tmp/x.db")
	t.Setenv("COAPLAB_ACK_TIMEOUT", "77ms")
	c.EnvOverride()
	if c.ListenAddr != "127.0.0.1:1234" || c.DBPath != "/tmp/x.db" || c.ACKTimeout != 77*time.Millisecond {
		t.Fatalf("env overrides not applied: %+v", c)
	}
}
