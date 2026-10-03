package pipeline

import (
	"fmt"
	"io"
	"sort"

	"rlmod/internal/config"
	"rlmod/internal/diag"
	"rlmod/internal/semdiff"
)

// VerifyResult is one scenario outcome.
type VerifyResult struct {
	Name   string
	OK     bool
	Detail string
}

// Verify runs all configured scenarios and returns per-scenario outcomes.
func Verify(cfg *config.Config, logw io.Writer, emitDiag bool) []VerifyResult {
	var out []VerifyResult
	for _, name := range sortedScenarioNames(cfg.Scenarios) {
		sc := cfg.Scenarios[name]
		oldSem := firstNonEmpty(sc.OldSemVer, cfg.CompileSemVer)
		newSem := firstNonEmpty(sc.NewSemVer, cfg.CompileSemVer)
		oldB, err := Load(sc.Old, oldSem, cfg.FingerprintSchema)
		if err != nil {
			out = append(out, VerifyResult{Name: name, OK: false, Detail: "old load: " + err.Error()})
			continue
		}
		newB, err := Load(sc.New, newSem, cfg.FingerprintSchema)
		if err != nil {
			out = append(out, VerifyResult{Name: name, OK: false, Detail: "new load: " + err.Error()})
			continue
		}
		d := Compare(oldB, newB)
		logger := diag.NewLogger(logw, "", emitDiag)
		EmitDiagnostics(logger, d)
		out = append(out, VerifyResult{
			Name: name, OK: expectedInvariants(name, d),
			Detail: fmt.Sprintf("changes=%d invalidated=%d reused=%d version_rejected=%v",
				len(d.Changes), len(d.Invalidated), len(d.ReuseAllowed), d.VersionRejected),
		})
	}
	return out
}

// expectedInvariants encodes scenario-specific claims, defense-in-depth next
// to the independent golden-file tests.
func expectedInvariants(name string, d *semdiff.Report) bool {
	switch name {
	case "private-body":
		return !d.VersionRejected && len(d.Changes) == 1 &&
			changeClass(d, "core.secret") == "private_body_only" &&
			len(d.Invalidated) == 1 && hasStr(d.Invalidated, "core.secret")
	case "inline-const":
		return changeClass(d, "core.Offset") == "inline_const" &&
			hasStr(d.Invalidated, "core.Offset") && hasStr(d.Invalidated, "core.Calc") &&
			hasStr(d.Invalidated, "app.Run") && !hasStr(d.Invalidated, "core.secret")
	case "generic-body":
		return changeClass(d, "core.Scale[int]") == "generic_body" &&
			hasStr(d.Invalidated, "core.Scale[int]") && hasStr(d.Invalidated, "core.Calc") &&
			hasStr(d.Invalidated, "app.Run")
	case "public-type":
		return changeClass(d, "core.Tag") == "public_signature" &&
			hasStr(d.Invalidated, "core.Tag")
	case "sensitive-const":
		return changeClass(d, "vault.Token") == "inline_const" &&
			hasStr(d.Invalidated, "vault.Token")
	case "version":
		return d.VersionRejected && len(d.ReuseAllowed) == 0 && len(d.Changes) == 0
	}
	return false
}

func changeClass(d *semdiff.Report, key string) string {
	for _, c := range d.Changes {
		if c.Module+"."+c.Name == key {
			return string(c.Class)
		}
	}
	return ""
}

func hasStr(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}

func sortedScenarioNames(m map[string]config.ScenarioPair) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
