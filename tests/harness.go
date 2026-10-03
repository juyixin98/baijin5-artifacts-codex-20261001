// Package tests contains INDEPENDENT black-box tests. Expected values are
// hand-authored here (not produced by the system under test), and fingerprint
// hashes are recomputed independently with crypto/sha256 instead of trusting
// the fp package's own helpers.
package tests

import (
	"os"
	"path/filepath"
	"testing"

	"rlc/internal/build"
	"rlc/internal/config"
	"rlc/internal/diag"
	"rlc/internal/fp"
	"rlc/internal/frontend"
	"rlc/internal/interp"
	"rlc/internal/ir"
	"rlc/internal/lower"
)

// project is an in-temp-dir synthetic project. Each test mutates one source
// file and asserts the resulting invalidation set.
type project struct {
	dir  string
	srcs map[string]string
	cfg  *config.Config
}

const basePricing = `module pricing;
pub const BULK_FACTOR: int = 2;
const BASE: int = 50;
fn private_adjust(x: int) -> int { return x + BASE; }
pub fn unit_price(cents: int) -> int { return private_adjust(cents); }
pub fn bulk_price(cents: int, qty: int) -> int {
	return unit_price(cents) * qty * BULK_FACTOR;
}
`

const baseReport = `module report;
import pricing;
pub generic fn first(x: T) -> T { return x; }
pub fn quote(cents: int, qty: int) -> int {
	let total: int = pricing::bulk_price(cents, qty);
	return first(total) + pricing::BULK_FACTOR;
}
`

const baseApp = `module app;
import report;
pub fn main(n: int) -> int { return report::quote(10, n); }
`

func setupProject(t *testing.T, srcs map[string]string) *project {
	t.Helper()
	dir := t.TempDir()
	srcDir := filepath.Join(dir, "src")
	if err := os.MkdirAll(srcDir, 0o755); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{}
	for name, content := range srcs {
		p := filepath.Join(srcDir, name)
		if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
		files[name] = name
	}
	cfgPath := filepath.Join(dir, "rlc.json")
	cfgJSON := `{
 "project":"p","source_dir":"src","cache_dir":"cache",
 "entry_module":"app","entry_func":"main",
 "modules":[
  {"name":"pricing","file":"pricing.rl"},
  {"name":"report","file":"report.rl"},
  {"name":"app","file":"app.rl"}]}`
	if err := os.WriteFile(cfgPath, []byte(cfgJSON), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(cfgPath)
	if err != nil {
		t.Fatal(err)
	}
	return &project{dir: dir, srcs: srcs, cfg: cfg}
}

func baseSources() map[string]string {
	return map[string]string{
		"pricing.rl": basePricing,
		"report.rl":  baseReport,
		"app.rl":     baseApp,
	}
}

func (p *project) build(t *testing.T) *build.Report {
	t.Helper()
	log := diag.NewLogger(nil, "test-req", "")
	rep, err := build.New(p.cfg, log).Build()
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	return rep
}

func (p *project) setSource(file, content string) {
	p.srcs[file] = content
	_ = os.WriteFile(filepath.Join(p.cfg.SourceDir, file), []byte(content), 0o644)
}

func (p *project) expectValue(t *testing.T, target string, arg, want int64) {
	t.Helper()
	rep := p.build(t)
	v, err := interp.New(rep.Program).Call(target, []ir.Value{{Type: "int", I: arg}})
	if err != nil {
		t.Fatalf("call %s: %v", target, err)
	}
	if v.Type != "int" || v.I != want {
		t.Fatalf("%s(%d) = %v, want int:%d", target, arg, v, want)
	}
}

func analyze(t *testing.T, srcs map[string]string) *lower.Analysis {
	t.Helper()
	mods := map[string]*frontend.Module{}
	for name, src := range srcs {
		m, err := frontend.Parse(name, src)
		if err != nil {
			t.Fatalf("parse %s: %v", name, err)
		}
		mods[m.Name] = m
	}
	an, err := lower.Analyze(mods, diag.NewLogger(nil, "t", ""))
	if err != nil {
		t.Fatalf("analyze: %v", err)
	}
	return an
}

func fpSet(t *testing.T, srcs map[string]string) *fp.FingerprintSet {
	return fp.Compute(analyze(t, srcs))
}

func assertSetEqual(t *testing.T, got []string, want []string, label string) {
	t.Helper()
	gm := map[string]bool{}
	for _, g := range got {
		gm[g] = true
	}
	wm := map[string]bool{}
	for _, w := range want {
		wm[w] = true
	}
	for w := range wm {
		if !gm[w] {
			t.Errorf("%s: missing %q; got %v", label, w, got)
		}
	}
	for g := range gm {
		if !wm[g] {
			t.Errorf("%s: unexpected %q; want %v", label, g, want)
		}
	}
}

func buildIR(an *lower.Analysis) ([]*lower.CompiledModule, []*ir.Func, error) {
	return lower.Compile(an)
}

func linkIR(an *lower.Analysis, mods []*lower.CompiledModule, specs []*ir.Func) *ir.Program {
	return lower.LinkProgram(an, mods, specs)
}
