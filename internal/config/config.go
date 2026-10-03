// Package config defines linker configuration independent of the CLI.
package config

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"
)

// Config is a single link/run configuration.
type Config struct {
	Entry    string
	Objects  []string
	MaxSteps int
	Verbose  bool
}

// Default returns a safe default configuration.
func Default() Config {
	return Config{Entry: "main", MaxSteps: 1_000_000}
}

// Parse reads a simple line-oriented config file:
//
//	entry = main
//	objects = a.o b.o c.o
//	maxsteps = 100000
//	verbose = true
//
// Lines starting with '#' are comments. Unknown keys are rejected so a
// misspelled key never silently disappears.
func Parse(r io.Reader) (Config, error) {
	cfg := Default()
	sc := bufio.NewScanner(r)
	ln := 0
	for sc.Scan() {
		ln++
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			return cfg, fmt.Errorf("config line %d: expected key = value", ln)
		}
		key = strings.TrimSpace(key)
		val = strings.TrimSpace(val)
		switch key {
		case "entry":
			cfg.Entry = val
		case "objects":
			cfg.Objects = strings.Fields(val)
		case "maxsteps":
			n, err := strconv.Atoi(val)
			if err != nil || n <= 0 {
				return cfg, fmt.Errorf("config line %d: maxsteps must be positive", ln)
			}
			cfg.MaxSteps = n
		case "verbose":
			cfg.Verbose = val == "true"
		default:
			return cfg, fmt.Errorf("config line %d: unknown key %q", ln, key)
		}
	}
	return cfg, sc.Err()
}
