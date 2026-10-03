// Package fp computes deterministic public-interface fingerprints. A
// fingerprint deliberately EXCLUDES a non-generic function body so that
// private implementation changes do not invalidate callers; inline constants
// and generic bodies are recorded as EXPLICIT interface dependencies.
package fp

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"

	"rlc/internal/ir"
	"rlc/internal/lower"
)

// SemVersion is the compilation semantic version. Fingerprints produced under
// a different SemVersion must never be reused across builds.
const SemVersion = "rlc-sem-1"

type Dependency struct {
	Kind   string `json:"kind"`
	Target string `json:"target"`
	Hash   string `json:"hash"`
}

type Fingerprint struct {
	SemVersion string `json:"sem_version"`
	Key        string `json:"key"`
	Public     bool   `json:"public"`
	Kind       string `json:"kind"`
	Signature  string `json:"signature"`
	// InterfaceHash covers signature plus explicit interface dependencies
	// (inline constants and generic bodies). It drives caller invalidation.
	InterfaceHash string `json:"interface_hash"`
	// ImplHash covers the private body / template. A change here rebuilds the
	// owner module but does not by itself invalidate ordinary callers.
	ImplHash string       `json:"impl_hash"`
	Depends  []Dependency `json:"depends"`
}

type FingerprintSet struct {
	SemVersion string                  `json:"sem_version"`
	Symbols    map[string]*Fingerprint `json:"symbols"`
}

func NewSet() *FingerprintSet {
	return &FingerprintSet{SemVersion: SemVersion, Symbols: map[string]*Fingerprint{}}
}

func hash(parts ...string) string {
	h := sha256.New()
	for i, p := range parts {
		if i > 0 {
			h.Write([]byte{0})
		}
		h.Write([]byte(p))
	}
	return hex.EncodeToString(h.Sum(nil))[:16]
}

// Compute builds fingerprints from analysis facts and resolved values.
func Compute(an *lower.Analysis) *FingerprintSet {
	set := NewSet()
	// First pass: record per-symbol signature/body hashes.
	base := map[string]*Fingerprint{}
	keys := []string{}
	for _, m := range an.Order {
		keys = append(keys, an.Modules[m].Order...)
	}
	for _, k := range keys {
		facts := factsOf(an, k)
		fp := &Fingerprint{
			SemVersion: SemVersion,
			Key:        k,
			Public:     facts.Pub,
			Kind:       string(facts.Kind),
			Signature:  facts.SigText,
			ImplHash:   hash("body", facts.BodyText),
		}
		if facts.Kind == lower.SymConst {
			// A PUBLIC inline constant value is part of the interface
			// contract; a private constant's value is implementation only.
			if facts.Pub {
				fp.Signature = facts.SigText + "=" + an.ConstDigest(k)
			}
		}
		base[k] = fp
	}
	// Second pass: resolve dependency target hashes (fixed-point over the
	// transitive interface hashes of explicit dependencies).
	for iter := 0; iter < len(keys)+2; iter++ {
		changed := false
		for _, k := range keys {
			fp := base[k]
			facts := factsOf(an, k)
			deps := make([]Dependency, 0, len(facts.Deps))
			depParts := []string{}
			for _, d := range facts.Deps {
				t, ok := base[d.Target]
				if !ok {
					continue
				}
				dh := t.InterfaceHash
				if dh == "" {
					dh = t.ImplHash // bootstrap iteration
				}
				deps = append(deps, Dependency{Kind: string(d.Kind), Target: d.Target, Hash: dh})
				depParts = append(depParts, string(d.Kind)+":"+d.Target+"@"+dh)
			}
			sort.Slice(deps, func(i, j int) bool {
				if deps[i].Kind != deps[j].Kind {
					return deps[i].Kind < deps[j].Kind
				}
				return deps[i].Target < deps[j].Target
			})
			sort.Strings(depParts)
			// Generic templates are compiled INTO every caller instantiation,
			// so the template body is part of the generic symbol's interface:
			// a generic body change must invalidate instantiating callers.
			bodyPart := ""
			if facts.Generic && facts.Kind == lower.SymFunc {
				bodyPart = fp.ImplHash
			}
			iface := hash(SemVersion, "iface", k, fp.Signature, bodyPart, strings.Join(depParts, "|"))
			if iface != fp.InterfaceHash {
				fp.InterfaceHash = iface
				fp.Depends = deps
				changed = true
			}
		}
		if !changed {
			break
		}
	}
	for k, fp := range base {
		set.Symbols[k] = fp
	}
	return set
}

// factsOf looks up facts for a symbol key.
func factsOf(an *lower.Analysis, k string) *lower.SymbolFacts {
	for _, m := range an.Order {
		ma := an.Modules[m]
		for _, f := range ma.Facts {
			if f.Key == k {
				return f
			}
		}
	}
	return nil
}

// ValueDigest renders a constant value deterministically for fingerprints.
func ValueDigest(v ir.Value) string {
	switch v.Type {
	case "int":
		return fmt.Sprintf("int:%d", v.I)
	case "bool":
		return fmt.Sprintf("bool:%t", v.B)
	case "string":
		return "string:" + hash(v.S)
	}
	return "?"
}
