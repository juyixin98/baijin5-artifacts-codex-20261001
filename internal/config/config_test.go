package config

import (
	"strings"
	"testing"
)

func TestParse(t *testing.T) {
	cfg, err := Parse(strings.NewReader("# c\nentry = _start\nobjects = a.o b.o\nmaxsteps = 42\nverbose = true\n"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Entry != "_start" || len(cfg.Objects) != 2 || cfg.MaxSteps != 42 || !cfg.Verbose {
		t.Fatalf("parsed config mismatch: %+v", cfg)
	}
}

func TestParseRejectsUnknownKey(t *testing.T) {
	if _, err := Parse(strings.NewReader("entty = main\n")); err == nil {
		t.Fatal("unknown key must error")
	}
}
