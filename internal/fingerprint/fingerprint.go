// Package fingerprint computes the public-interface fingerprints of linked RL
// modules.
//
// Contract (part of the module's observable interface, documented on purpose):
//
//   - Ordinary functions are linked BY NAME. A change inside a function body
//     that leaves its signature and its inlined dependencies untouched moves
//     only that function's BodyHash. Private helper body changes therefore do
//     not invalidate any importer.
//   - Consts are folded and INLINED at each use site. A const value is an
//     interface-level event: callers that reference it embed the value, so the
//     const reference is part of the caller's InterfaceHash.
//   - Generic functions compile by MONOMORPHIZATION. Each concrete
//     instantiation has its own fingerprint that INCLUDES the instantiated
//     body, and callers depend on the exact instantiation they use.
//   - Exported type surface changes change the module's InterfaceHash.
//
// Hash: SHA-256 over deterministic canonical text; reports show prefixes.
package fingerprint

import (
	"crypto/sha256"
	"encoding/hex"
	"rlmod/internal/ir"
	"sort"
	"strconv"
	"strings"
)

const (
	PublicHashLen = 16
	SchemaVersion = "fp-v1"
)

// TypeFP is the fingerprint of one symbol (see package doc for semantics).
type TypeFP struct {
	Kind          string
	Module        string
	Name          string
	Public        bool
	Hash          string
	InterfaceHash string
	BodyHash      string
	Short         string
	Details       map[string]string
}

// ModuleFP aggregates symbol fingerprints.
type ModuleFP struct {
	Name       string
	PublicHash string // public interface surface only
	FullHash   string // interface + private symbols' bodies
	Symbols    map[string]*TypeFP
	PublicKeys []string
}

// Report holds per-module and program-wide fingerprints.
type Report struct {
	Schema      string
	SemVer      string
	Modules     map[string]*ModuleFP
	ProgramHash string
}

func hashText(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func shortHash(x string) string {
	if len(x) > PublicHashLen {
		return x[:PublicHashLen]
	}
	return x
}

// Compute builds the fingerprint report for a linked program.
func Compute(prog *ir.Program) *Report {
	r := &Report{Schema: prog.SchemaVersion, SemVer: prog.SemVer, Modules: map[string]*ModuleFP{}}
	for _, mname := range prog.Order {
		mod := prog.Modules[mname]
		mfp := &ModuleFP{Name: mname, Symbols: map[string]*TypeFP{}}

		for _, n := range sortedKeysType(mod.Types) {
			td := mod.Types[n]
			iface := canonLines("base="+td.Base.String(), "kind=type", "module="+td.Module, "name="+td.Name)
			ih := hashText(iface)
			mfp.Symbols[n] = &TypeFP{Kind: "type", Module: mname, Name: n, Public: ir.Exported(n),
				InterfaceHash: ih, BodyHash: "", Hash: ih, Short: shortHash(ih),
				Details: map[string]string{"base": td.Base.String()}}
		}

		for _, n := range sortedKeysConst(mod.Consts) {
			cd := mod.Consts[n]
			val := "int:" + strconv.FormatInt(cd.IntVal, 10)
			if int(cd.Kind) == 1 {
				val = "str:" + cd.StrVal
			}
			iface := canonLines("inline_value="+val, "kind=const", "module="+cd.Module,
				"name="+cd.Name, "type="+cd.Type.String())
			ih := hashText(iface)
			mfp.Symbols[n] = &TypeFP{Kind: "const", Module: mname, Name: n, Public: ir.Exported(n),
				InterfaceHash: ih, BodyHash: "", Hash: ih, Short: shortHash(ih),
				Details: map[string]string{"type": cd.Type.String(), "inline_value": val, "sensitive": btoa(cd.Sensitive)}}
		}

		for _, k := range sortedKeysFunc(mod.Funcs) {
			mfp.Symbols[k] = SymbolFunc(mname, mod.Funcs[k])
		}

		var pubIface, fullIface, bodies []string
		for key, sf := range mfp.Symbols {
			// Full module identity includes every symbol interface and every
			// function body; the public hash includes only EXPORTED symbols'
			// interface surface, never private symbols or any function body.
			fullIface = append(fullIface, sf.Kind+"|"+sf.Name+"|"+sf.InterfaceHash)
			if sf.BodyHash != "" {
				bodies = append(bodies, sf.Kind+"|"+sf.Name+"|"+sf.BodyHash)
			}
			if sf.Public {
				mfp.PublicKeys = append(mfp.PublicKeys, key)
				pubIface = append(pubIface, sf.Kind+"|"+sf.Name+"|"+sf.InterfaceHash)
			}
		}
		sort.Strings(mfp.PublicKeys)
		mfp.PublicHash = shortHash(hashText(canonLines(pubIface...)))
		mfp.FullHash = shortHash(hashText(canonLines(append(append([]string{}, fullIface...), bodies...)...)))
		r.Modules[mname] = mfp
	}
	var parts []string
	for _, mname := range prog.Order {
		parts = append(parts, mname+"="+r.Modules[mname].FullHash)
	}
	r.ProgramHash = shortHash(hashText(canonLines(parts...)))
	return r
}

// SymbolFunc fingerprints one function instance.
func SymbolFunc(mname string, f *ir.Func) *TypeFP {
	var params []string
	for _, p := range f.Params {
		params = append(params, p.Name+":"+p.Type.String())
	}
	res := ""
	if f.HasResult {
		res = f.Result.String()
	}
	body := CanonicalBody(f)
	bodyHash := hashText(body)

	ifaceLines := []string{
		"generic=" + btoa(f.Generic),
		"kind=func",
		"module=" + mname,
		"name=" + f.Key,
		"params=" + strings.Join(params, ","),
		"result=" + res,
	}
	// What the caller physically embeds: inlined const values and exact
	// generic instantiations. Ordinary (name-linked) call deps are NOT part of
	// the interface hash. The dependency lists are sorted and filtered to
	// PUBLIC symbols so a private helper cannot leak into the public hash.
	ifaceLines = append(ifaceLines, "const_deps="+strings.Join(publicDeps(f.ConstDeps()), ","))
	ifaceLines = append(ifaceLines, "generic_deps="+strings.Join(publicDeps(f.GenericDeps()), ","))
	if f.Generic {
		// The monomorphized body is copied into the caller.
		ifaceLines = append(ifaceLines, "generic_body="+bodyHash)
	}
	ih := hashText(canonLines(ifaceLines...))
	full := hashText(canonLines("interface="+ih, "body="+bodyHash))
	return &TypeFP{Kind: kindOf(f), Module: mname, Name: f.Key, Public: ir.Exported(f.OrigName),
		InterfaceHash: ih, BodyHash: bodyHash, Hash: full, Short: shortHash(full),
		Details: map[string]string{
			"params": strings.Join(params, ","), "result": res, "generic": btoa(f.Generic),
			"const_deps":   strings.Join(f.ConstDeps(), ","),
			"call_deps":    strings.Join(f.CallDeps(), ","),
			"generic_deps": strings.Join(f.GenericDeps(), ","),
			"body":         shortHash(bodyHash),
		}}
}

func kindOf(f *ir.Func) string {
	if f.Generic {
		return "generic"
	}
	return "func"
}

// publicDeps keeps dependencies whose symbol name (the part after the final
// ".") is exported, so private dependencies never enter a public fingerprint.
func publicDeps(deps []string) []string {
	var out []string
	for _, d := range deps {
		name := d
		if i := strings.LastIndex(d, "."); i >= 0 {
			name = d[i+1:]
		}
		if ir.Exported(name) {
			out = append(out, d)
		}
	}
	sort.Strings(out)
	return out
}
