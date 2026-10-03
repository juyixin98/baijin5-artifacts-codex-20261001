// Package pipeline ties the language frontend, IR lowering, fingerprints,
// semantic diff and the interpreter into one local, file-based workflow.
package pipeline

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"rlmod/internal/diag"
	"rlmod/internal/fingerprint"
	"rlmod/internal/frontend"
	"rlmod/internal/ir"
	"rlmod/internal/runtime"
	"rlmod/internal/semdiff"
)

// Build is one compiled snapshot.
type Build struct {
	Program *ir.Program
	Report  *fingerprint.Report
	Sources map[string]string
	Order   []string
}

// Load reads all *.rl files under dir, parses and links them. semver/schema
// are attached for fingerprint/version gating.
func Load(dir, semver, schema string) (*Build, error) {
	files, err := filepath.Glob(filepath.Join(dir, "*.rl"))
	if err != nil {
		return nil, err
	}
	if len(files) == 0 {
		return nil, fmt.Errorf("no .rl files found in %s", dir)
	}
	sort.Strings(files)
	parsed := map[string]*frontend.Program{}
	sources := map[string]string{}
	var order []string
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			return nil, err
		}
		prog, err := frontend.Parse(string(raw))
		if err != nil {
			return nil, fmt.Errorf("%s: %w", filepath.Base(f), err)
		}
		if _, dup := parsed[prog.Module]; dup {
			return nil, fmt.Errorf("duplicate module %q in %s", prog.Module, filepath.Base(f))
		}
		parsed[prog.Module] = prog
		sources[prog.Module] = string(raw)
		order = append(order, prog.Module)
	}
	p, err := ir.Build(parsed, order, semver, schema)
	if err != nil {
		return nil, err
	}
	return &Build{Program: p, Report: fingerprint.Compute(p), Sources: sources, Order: order}, nil
}

// Run executes the snapshot's entrypoint.
func (b *Build) Run() (runtime.Value, error) {
	return runtime.New(b.Program).Run()
}

// Call invokes a named exported function with integer arguments.
func (b *Build) Call(fn string, ints []int64) (runtime.Value, error) {
	args := make([]runtime.Value, 0, len(ints))
	for _, v := range ints {
		args = append(args, runtime.IntVal(v))
	}
	return runtime.New(b.Program).CallWith("", fn, args)
}

// Compare returns the semantic-diff report between two builds.
func Compare(oldB, newB *Build) *semdiff.Report {
	return semdiff.Diff(oldB.Program, newB.Program, oldB.Report, newB.Report)
}

// EmitDiagnostics writes accept/reject/inconclusive decisions for a diff to
// the logger, one record per invalidated and reused unit.
func EmitDiagnostics(l *diag.Logger, diff *semdiff.Report) {
	if diff.VersionRejected {
		l.Log("warn", diag.Inconclusive, "version-gate", diff.VersionReason, map[string]string{
			"old_schema": diff.OldSchema, "new_schema": diff.NewSchema,
			"old_semver": diff.OldSemVer, "new_semver": diff.NewSemVer,
		})
	}
	inv := map[string]bool{}
	for _, x := range diff.Invalidated {
		inv[x] = true
	}
	for _, ch := range diff.Changes {
		subject := ch.Module + "." + ch.Name
		state := map[string]string{"class": string(ch.Class), "kind": ch.Kind}
		if ch.From != "" {
			state["from"] = ch.From
		}
		if ch.To != "" {
			state["to"] = ch.To
		}
		// Final verdict is membership in the propagated invalidation set:
		// a symbol may change for a private-only reason yet still require
		// recompilation because it inlined another changed dependency.
		decision := diag.Accept
		if inv[subject] {
			decision = diag.Reject
		}
		state["in_invalidation_set"] = boolStr(inv[subject])
		if diff.VersionRejected {
			decision = diag.Inconclusive
		}
		l.Log("info", decision, subject, ch.Reason, state)
	}
	for _, unit := range diff.ReuseAllowed {
		l.Log("debug", diag.Accept, unit, "fingerprint unchanged; reuse old compilation", map[string]string{"in_invalidation_set": "false"})
	}
	if diff.VersionRejected {
		for _, unit := range diff.Invalidated {
			l.Log("warn", diag.Inconclusive, unit, "cannot judge reuse across compile semantic version boundary", map[string]string{"in_invalidation_set": "true"})
		}
	} else {
		for _, unit := range diff.Invalidated {
			l.Log("info", diag.Reject, unit, "in minimal invalidation set; must recompile", map[string]string{"in_invalidation_set": "true"})
		}
	}
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}
