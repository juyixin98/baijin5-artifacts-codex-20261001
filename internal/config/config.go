// Package config loads project configuration. Configuration is deliberately
// data-only: module list, source directory and run entry point.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type ModuleSpec struct {
	Name string `json:"name"`
	File string `json:"file"`
}

type Config struct {
	Project     string       `json:"project"`
	SourceDir   string       `json:"source_dir"`
	CacheDir    string       `json:"cache_dir"`
	EntryModule string       `json:"entry_module"`
	EntryFunc   string       `json:"entry_func"`
	Modules     []ModuleSpec `json:"modules"`
	// RedactLiterals turns on secret-style redaction in diagnostics.
	RedactLiterals bool `json:"redact_literals"`
}

func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config %s: %w", path, err)
	}
	var c Config
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, fmt.Errorf("parse config %s: %w", path, err)
	}
	if c.SourceDir == "" {
		c.SourceDir = "src"
	}
	if c.CacheDir == "" {
		c.CacheDir = ".rlc-cache"
	}
	if c.EntryFunc == "" {
		c.EntryFunc = "main"
	}
	if len(c.Modules) == 0 {
		return nil, fmt.Errorf("config %s: no modules declared", path)
	}
	seen := map[string]bool{}
	for _, m := range c.Modules {
		if m.Name == "" || m.File == "" {
			return nil, fmt.Errorf("config %s: module needs name and file", path)
		}
		if seen[m.Name] {
			return nil, fmt.Errorf("config %s: duplicate module %s", path, m.Name)
		}
		seen[m.Name] = true
	}
	base := filepath.Dir(path)
	c.SourceDir = resolve(base, c.SourceDir)
	c.CacheDir = resolve(base, c.CacheDir)
	return &c, nil
}

func resolve(base, p string) string {
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Clean(filepath.Join(base, p))
}
