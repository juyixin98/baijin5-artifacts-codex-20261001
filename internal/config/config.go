// Package config holds tunable limits for the specializer and interpreter.
// Defaults are chosen deterministically; every field can be overridden from a
// JSON file or an environment variable, so no test depends on magic numbers
// scattered through the code.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
)

// Config is the complete local configuration.
type Config struct {
	// MaxVariantsPerFunc caps residual variants for one function. When reached,
	// further static patterns are generalized into a single dynamic variant.
	MaxVariantsPerFunc int `json:"max_variants_per_func"`
	// MaxResidualNodes is the global code-size budget measured in IR nodes.
	MaxResidualNodes int `json:"max_residual_nodes"`
	// FoldBudget bounds how many pure user-function calls may be executed at
	// compile time for one specialization request; exhaustion forces
	// residualization (a semantic-preserving fallback).
	FoldBudget int `json:"fold_budget"`
	// MaxCallDepth bounds interpreter recursion at run time.
	MaxCallDepth int `json:"max_call_depth"`
	// StrictBudget makes an exhausted global code budget a hard E_BUDGET error
	// instead of silently falling back to the un-specialized program.
	StrictBudget bool `json:"strict_budget"`
}

// Default returns deterministic default settings.
func Default() Config {
	return Config{
		MaxVariantsPerFunc: 16,
		MaxResidualNodes:   4096,
		FoldBudget:         50_000,
		MaxCallDepth:       200_000,
		StrictBudget:       false,
	}
}

// Load applies JSON-file overrides (path may be empty) and FUNCSPEC_*
// environment variables on top of Default.
func Load(path string) (Config, error) {
	c := Default()
	if path != "" {
		raw, err := os.ReadFile(path)
		if err != nil {
			return c, fmt.Errorf("read config %s: %w", path, err)
		}
		if err := json.Unmarshal(raw, &c); err != nil {
			return c, fmt.Errorf("parse config %s: %w", path, err)
		}
	}
	applyInt := func(env string, dst *int) {
		if v, ok := os.LookupEnv(env); ok {
			n, err := strconv.Atoi(v)
			if err == nil {
				*dst = n
			}
		}
	}
	applyInt("FUNCSPEC_MAX_VARIANTS", &c.MaxVariantsPerFunc)
	applyInt("FUNCSPEC_MAX_NODES", &c.MaxResidualNodes)
	applyInt("FUNCSPEC_FOLD_BUDGET", &c.FoldBudget)
	applyInt("FUNCSPEC_MAX_DEPTH", &c.MaxCallDepth)
	if v, ok := os.LookupEnv("FUNCSPEC_STRICT_BUDGET"); ok {
		c.StrictBudget = v == "1" || v == "true"
	}
	if err := c.Validate(); err != nil {
		return c, err
	}
	return c, nil
}

// Validate rejects non-positive limits.
func (c Config) Validate() error {
	if c.MaxVariantsPerFunc <= 0 {
		return fmt.Errorf("max_variants_per_func must be > 0")
	}
	if c.MaxResidualNodes <= 0 {
		return fmt.Errorf("max_residual_nodes must be > 0")
	}
	if c.FoldBudget <= 0 {
		return fmt.Errorf("fold_budget must be > 0")
	}
	if c.MaxCallDepth <= 0 {
		return fmt.Errorf("max_call_depth must be > 0")
	}
	return nil
}
