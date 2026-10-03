// Package build orchestrates parsing, analysis, fingerprinting, lowering,
// incremental caching and minimal invalidation-set computation.
package build

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"rlc/internal/config"
	"rlc/internal/diag"
	"rlc/internal/fp"
	"rlc/internal/frontend"
	"rlc/internal/ir"
	"rlc/internal/lower"
)

type SymbolChange struct {
	Key      string `json:"key"`
	Module   string `json:"module"`
	Category string `json:"category"`
	Detail   string `json:"detail"`
}

type Report struct {
	RequestID       string              `json:"request_id"`
	SemVersion      string              `json:"semantic_version"`
	FullRebuild     bool                `json:"full_rebuild"`
	ChangedSymbols  []SymbolChange      `json:"changed_symbols"`
	InvalidatedSet  []string            `json:"minimal_invalidated_set"`
	InvalidatedBy   map[string][]string `json:"invalidated_by"`
	ChangedModules  []string            `json:"changed_modules"`
	ReusedModules   []string            `json:"reused_modules"`
	CacheVerdict    string              `json:"cache_verdict"`
	CompiledSymbols int                 `json:"compiled_symbols"`
	Program         *ir.Program         `json:"-"`
}

type cacheFile struct {
	SemVersion   string             `json:"semantic_version"`
	Sources      map[string]string  `json:"sources"`
	Fingerprints *fp.FingerprintSet `json:"fingerprints"`
	Program      []byte             `json:"program"`
}

type Builder struct {
	cfg *config.Config
	log *diag.Logger
}

func New(cfg *config.Config, log *diag.Logger) *Builder {
	return &Builder{cfg: cfg, log: log}
}

func sourceDigest(src string) string {
	sum := sha256.Sum256([]byte(src))
	return hex.EncodeToString(sum[:])[:16]
}

func (b *Builder) cachePath() string { return filepath.Join(b.cfg.CacheDir, "cache.json") }

func (b *Builder) readSources() (map[string]*frontend.Module, map[string]string, error) {
	srcs := map[string]string{}
	mods := map[string]*frontend.Module{}
	for _, m := range b.cfg.Modules {
		path := filepath.Join(b.cfg.SourceDir, m.File)
		raw, err := os.ReadFile(path)
		if err != nil {
			if b.log != nil {
				b.log.WithModule(m.Name).Reject(diag.CatParse, m.Name, "cannot read source",
					map[string]any{"path": path})
			}
			return nil, nil, fmt.Errorf("read %s: %w", path, err)
		}
		ast, err := frontend.Parse(m.File, string(raw))
		if err != nil {
			if b.log != nil {
				b.log.WithModule(m.Name).Reject(diag.CatParse, m.Name, "parse failed",
					map[string]any{"error": err.Error()})
			}
			return nil, nil, err
		}
		if ast.Name != m.Name {
			return nil, nil, fmt.Errorf("%s: declares module %q, config expects %q", m.File, ast.Name, m.Name)
		}
		srcs[m.Name] = sourceDigest(string(raw))
		mods[m.Name] = ast
	}
	return mods, srcs, nil
}

func (b *Builder) loadCache() (*cacheFile, string) {
	data, err := os.ReadFile(b.cachePath())
	if err != nil {
		return nil, "no_cache"
	}
	var c cacheFile
	if err := json.Unmarshal(data, &c); err != nil {
		if b.log != nil {
			b.log.Reject(diag.CatCacheCorrupt, "", "cache unreadable; full rebuild",
				map[string]any{"path": b.cachePath()})
		}
		return nil, "corrupt_cache"
	}
	if c.SemVersion != fp.SemVersion {
		if b.log != nil {
			b.log.Reject(diag.CatStaleSemVer, "", "stale semantic version; old fingerprints rejected",
				map[string]any{"cached": c.SemVersion, "current": fp.SemVersion})
		}
		return nil, "stale_semantic_version"
	}
	if c.Fingerprints == nil || c.Fingerprints.SemVersion != fp.SemVersion {
		b.log.Reject(diag.CatStaleSemVer, "", "fingerprint semantic version mismatch", nil)
		return nil, "stale_semantic_version"
	}
	return &c, "cache_hit"
}

// Build performs analysis/fingerprinting/lowering and returns an incremental
// report. On the first build (or stale cache) it is a full rebuild.
func (b *Builder) Build() (*Report, error) {
	reqID := ""
	if b.log != nil {
		reqID = b.log.RequestID()
	}
	rep := &Report{RequestID: reqID, SemVersion: fp.SemVersion, InvalidatedBy: map[string][]string{}}

	mods, srcDigests, err := b.readSources()
	if err != nil {
		return nil, err
	}
	an, err := lower.Analyze(mods, b.log)
	if err != nil {
		if b.log != nil {
			b.log.Reject(diag.CatType, "", "semantic analysis rejected program",
				map[string]any{"error": err.Error()})
		}
		return nil, err
	}
	newFp := fp.Compute(an)

	old, verdict := b.loadCache()
	if old == nil {
		rep.FullRebuild = true
		rep.CacheVerdict = verdict
		// No usable prior fingerprints: every symbol is in the full
		// compilation set.
		for k := range newFp.Symbols {
			rep.InvalidatedSet = append(rep.InvalidatedSet, k)
		}
		rep.ChangedModules = append(rep.ChangedModules, an.Order...)
	} else {
		rep.CacheVerdict = verdict
		changes, inval, by := b.diffFingerprints(old.Fingerprints, newFp)
		rep.ChangedSymbols = changes
		rep.InvalidatedSet = inval
		rep.InvalidatedBy = by
		// Source digest changes even when fingerprints coincide (whitespace).
		changedModSet := map[string]bool{}
		for m, d := range srcDigests {
			if old.Sources[m] != d {
				changedModSet[m] = true
			} else {
				// module content byte-identical: reusable
			}
		}
		// Any module owning an invalidated symbol must be rebuilt/relinked.
		ownerSet := map[string]bool{}
		for _, k := range inval {
			ownerSet[owner(k)] = true
		}
		for _, ch := range changes {
			changedModSet[ch.Module] = true
			ownerSet[ch.Module] = true
		}
		for _, m := range an.Order {
			if ownerSet[m] || changedModSet[m] {
				rep.ChangedModules = append(rep.ChangedModules, m)
			} else {
				rep.ReusedModules = append(rep.ReusedModules, m)
			}
		}
	}

	compMods, specs, err := lower.Compile(an)
	if err != nil {
		if b.log != nil {
			b.log.Reject(diag.CatType, "", "lowering failed", map[string]any{"error": err.Error()})
		}
		return nil, err
	}
	prog := lower.LinkProgram(an, compMods, specs)
	rep.Program = prog
	rep.CompiledSymbols = len(newFp.Symbols)

	sort.Strings(rep.InvalidatedSet)
	sort.Strings(rep.ChangedModules)
	sort.Strings(rep.ReusedModules)
	sort.Slice(rep.ChangedSymbols, func(i, j int) bool { return rep.ChangedSymbols[i].Key < rep.ChangedSymbols[j].Key })

	if err := b.writeCache(srcDigests, newFp, prog); err != nil {
		if b.log != nil {
			b.log.Undecidable(diag.CatCacheCorrupt, "", "could not persist cache", map[string]any{"error": err.Error()})
		}
	}
	if b.log != nil {
		b.log.Accept("", "build accepted", map[string]any{
			"full_rebuild":    rep.FullRebuild,
			"changed_symbols": len(rep.ChangedSymbols),
			"invalidated":     len(rep.InvalidatedSet),
			"reused_modules":  len(rep.ReusedModules),
		})
	}
	return rep, nil
}

func owner(key string) string {
	// module is everything before the last dot; strip generic suffix.
	k := key
	if i := lastAngle(k); i >= 0 {
		k = k[:i]
	}
	for i := len(k) - 1; i >= 0; i-- {
		if k[i] == '.' {
			return k[:i]
		}
	}
	return k
}

func lastAngle(s string) int {
	for i := len(s) - 1; i >= 0; i-- {
		if s[i] == '<' {
			return i
		}
	}
	return -1
}

// diffFingerprints classifies direct edits and computes the propagated
// invalidation set through explicit interface dependencies.
func (b *Builder) diffFingerprints(old, new *fp.FingerprintSet) ([]SymbolChange, []string, map[string][]string) {
	changes := []SymbolChange{}
	changedSet := map[string]bool{}       // every directly edited symbol
	interfaceChanged := map[string]bool{} // symbols whose public interface moved

	keys := map[string]bool{}
	for k := range old.Symbols {
		keys[k] = true
	}
	for k := range new.Symbols {
		keys[k] = true
	}
	for k := range keys {
		o, nok := old.Symbols[k]
		n, nnk := new.Symbols[k]
		switch {
		case !nok:
			changes = append(changes, SymbolChange{Key: k, Module: owner(k), Category: "symbol_added", Detail: "new symbol"})
			changedSet[k] = true
			interfaceChanged[k] = true
		case !nnk:
			changes = append(changes, SymbolChange{Key: k, Module: owner(k), Category: "symbol_removed", Detail: "removed symbol"})
			changedSet[k] = true
			interfaceChanged[k] = true
		case o.InterfaceHash == n.InterfaceHash && o.ImplHash == n.ImplHash:
			// unchanged
		case o.InterfaceHash != n.InterfaceHash:
			cat, detail := classifyInterface(o, n)
			changes = append(changes, SymbolChange{Key: k, Module: owner(k), Category: cat, Detail: detail})
			changedSet[k] = true
			interfaceChanged[k] = true
		case o.ImplHash != n.ImplHash:
			cat := "private_body_change"
			detail := "implementation body changed; interface fingerprint stable"
			if !o.Public {
				cat = string(diag.CatPrivateBody)
			}
			changes = append(changes, SymbolChange{Key: k, Module: owner(k), Category: cat, Detail: detail})
			changedSet[k] = true
		}
	}

	// Reverse edges from explicit dependency declarations.
	reverse := map[string][]string{}
	for _, f := range new.Symbols {
		for _, d := range f.Depends {
			reverse[d.Target] = append(reverse[d.Target], f.Key)
		}
	}
	for k := range reverse {
		sort.Strings(reverse[k])
	}

	invalidated := map[string]bool{}
	by := map[string][]string{}
	// Only interface changes propagate. A private/body-only change rebuilds
	// the owning module but never invalidates callers on its own.
	queue := []string{}
	for k := range interfaceChanged {
		invalidated[k] = true
		queue = append(queue, k)
	}
	// Body-only edits are themselves invalidated but have no out-edges.
	for k := range changedSet {
		invalidated[k] = true
	}
	for len(queue) > 0 {
		k := queue[0]
		queue = queue[1:]
		for _, dep := range reverse[k] {
			if !invalidated[dep] {
				invalidated[dep] = true
				by[dep] = append(by[dep], k)
				queue = append(queue, dep)
			} else if !contains(by[dep], k) {
				by[dep] = append(by[dep], k)
			}
		}
	}
	set := []string{}
	for k := range invalidated {
		set = append(set, k)
	}
	sort.Strings(set)
	for k := range by {
		sort.Strings(by[k])
	}
	return changes, set, by
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

func classifyInterface(o, n *fp.Fingerprint) (string, string) {
	// Generic body change surfaces as interface change because the template
	// body is in the generic interface hash.
	if o.Signature != n.Signature {
		return string(diag.CatFingerprint), "public type/signature or inline constant value changed"
	}
	// Dependency targets hashes changed (transitive inline const / generic body).
	return string(diag.CatSemanticChange), "interface dependency changed (inline const or generic body)"
}

func (b *Builder) writeCache(srcs map[string]string, fps *fp.FingerprintSet, prog *ir.Program) error {
	if err := os.MkdirAll(b.cfg.CacheDir, 0o755); err != nil {
		return err
	}
	pdata, err := ir.MarshalProgram(prog)
	if err != nil {
		return err
	}
	cf := cacheFile{SemVersion: fp.SemVersion, Sources: srcs, Fingerprints: fps, Program: pdata}
	data, err := json.MarshalIndent(&cf, "", "  ")
	if err != nil {
		return err
	}
	tmp := b.cachePath() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, b.cachePath())
}

// LoadCachedProgram reads the last successfully linked program.
func (b *Builder) LoadCachedProgram() (*ir.Program, error) {
	data, err := os.ReadFile(b.cachePath())
	if err != nil {
		return nil, err
	}
	var cf cacheFile
	if err := json.Unmarshal(data, &cf); err != nil {
		return nil, err
	}
	if cf.SemVersion != fp.SemVersion {
		return nil, fmt.Errorf("cache semantic version %s != %s", cf.SemVersion, fp.SemVersion)
	}
	return ir.UnmarshalProgram(cf.Program)
}
