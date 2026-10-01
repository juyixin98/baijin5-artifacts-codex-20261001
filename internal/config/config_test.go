package config_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ntpsim/internal/config"
)

func validJSON() string {
	return `{
	  "sources": [
	    {"name":"a","stratum":2,"li":0,"reference_id":1,"offset":"10ms"}
	  ],
	  "links": [
	    {"source":"a","to_server":"5ms","from_server":"7ms","loss_rate":0.1}
	  ],
	  "policy": {"max_sample_age":"30s","max_rtt":"2s","max_clock_jump":"200ms",
	             "rtt_outlier_factor":3,"min_sources":1},
	  "run": {"start":"2024-06-01T12:00:00Z","rounds":2,"poll_interval":"10s",
	          "reply_timeout":"1s","epsilon":"50us","link_seed":1}
	}`
}

func writeTemp(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "c.json")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestLoadValid(t *testing.T) {
	c, err := config.Load(writeTemp(t, validJSON()))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if c.Sources[0].Offset.Duration() != 10*time.Millisecond {
		t.Fatalf("offset=%v", c.Sources[0].Offset.Duration())
	}
	if c.Run.ReplyTimeout.Duration() != time.Second {
		t.Fatalf("timeout=%v", c.Run.ReplyTimeout.Duration())
	}
	p := c.PolicyOptions()
	if p.MaxRTT != 2*time.Second || p.RTTOutlierFactor != 3 {
		t.Fatalf("policy=%+v", p)
	}
	if got := c.StartTime(); got.Year() != 2024 {
		t.Fatalf("start=%v", got)
	}
}

func TestLoadRejectsBadInputs(t *testing.T) {
	cases := map[string]string{
		"no sources":          `{"sources":[],"run":{"start":"2024-06-01T12:00:00Z","rounds":1,"poll_interval":"1s","reply_timeout":"1s"}}`,
		"duplicate source":    `{"sources":[{"name":"a"},{"name":"a"}],"run":{"start":"2024-06-01T12:00:00Z","rounds":1,"poll_interval":"1s","reply_timeout":"1s"}}`,
		"bad li":              strings.Replace(validJSON(), `"li":0`, `"li":9`, 1),
		"unknown link source": strings.Replace(validJSON(), `"source":"a"`, `"source":"zzz"`, 1),
		"bad loss":            strings.Replace(validJSON(), `"loss_rate":0.1`, `"loss_rate":1.5`, 1),
		"zero rounds":         strings.Replace(validJSON(), `"rounds":2`, `"rounds":0`, 1),
		"negative interval":   strings.Replace(validJSON(), `"poll_interval":"10s"`, `"poll_interval":"-1s"`, 1),
		"bad factor":          strings.Replace(validJSON(), `"rtt_outlier_factor":3`, `"rtt_outlier_factor":0.5`, 1),
		"bad start":           strings.Replace(validJSON(), `"start":"2024-06-01T12:00:00Z"`, `"start":"nope"`, 1),
		"unknown field":       strings.Replace(validJSON(), `"link_seed":1`, `"link_seed":1,"bogus":1`, 1),
		"bad duration":        strings.Replace(validJSON(), `"offset":"10ms"`, `"offset":"ten"`, 1),
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := config.Load(writeTemp(t, body)); err == nil {
				t.Fatalf("%s: expected validation error", name)
			}
		})
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := config.Load(filepath.Join(t.TempDir(), "nope.json")); err == nil {
		t.Fatal("expected read error")
	}
}
