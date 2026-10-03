package tests

import (
	"os"
	"strings"
	"testing"

	"rlc/internal/build"
)

// TestPrivateBodyDoesNotInvalidateCallers is the central ABI claim: changing a
// function's private implementation body leaves its interface hash stable and
// must not invalidate any downstream caller.
func TestPrivateBodyDoesNotInvalidateCallers(t *testing.T) {
	p := setupProject(t, baseSources())
	first := p.build(t)
	if !first.FullRebuild {
		t.Fatal("first build must be full")
	}
	// Sanity: concrete expected results, hand-computed independently.
	// unit_price(10)=60, bulk_price(10,2)=240, quote(10,3)=362.
	p.expectValue(t, "app.main", 3, 362)

	// No-op rebuild: zero invalidation.
	noop := p.build(t)
	if noop.FullRebuild || len(noop.InvalidatedSet) != 0 {
		t.Fatalf("no-op rebuild should invalidate nothing, got %v", noop.InvalidatedSet)
	}

	// Edit private body of a PUBLIC fn (+ 0 is semantically neutral).
	modified := strings.Replace(basePricing,
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents); }",
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents) + 0; }", 1)
	p.setSource("pricing.rl", modified)
	rep := p.build(t)

	if len(rep.ChangedSymbols) != 1 {
		t.Fatalf("want exactly 1 changed symbol, got %+v", rep.ChangedSymbols)
	}
	ch := rep.ChangedSymbols[0]
	if ch.Key != "pricing.unit_price" {
		t.Fatalf("changed key = %s", ch.Key)
	}
	if ch.Category != "private_body_change" {
		t.Fatalf("category = %s, want private_body_change", ch.Category)
	}
	// Minimal necessary invalidation set: only the owner symbol.
	assertSetEqual(t, rep.InvalidatedSet, []string{"pricing.unit_price"}, "invalidated")
	// Downstream modules must be reusable, not recompiled.
	for _, m := range []string{"report", "app"} {
		found := false
		for _, r := range rep.ReusedModules {
			if r == m {
				found = true
			}
		}
		if !found {
			t.Errorf("module %s should be reused; changed=%v reused=%v", m, rep.ChangedModules, rep.ReusedModules)
		}
	}
	// Result unchanged.
	p.expectValue(t, "app.main", 3, 362)
}

// TestPrivateConstIsBodyOnly: private constant value edits do not appear in
// any interface fingerprint.
func TestPrivateConstIsBodyOnly(t *testing.T) {
	p := setupProject(t, baseSources())
	p.build(t)
	modified := strings.Replace(basePricing, "const BASE: int = 50;", "const BASE: int = 51;", 1)
	p.setSource("pricing.rl", modified)
	rep := p.build(t)
	assertSetEqual(t, rep.InvalidatedSet, []string{"pricing.BASE"}, "invalidated")
	// pricing module rebuilt; report/app reused.
	if len(rep.ChangedModules) != 1 || rep.ChangedModules[0] != "pricing" {
		t.Fatalf("only pricing may rebuild, got %v", rep.ChangedModules)
	}
}

// TestInlineConstPropagates is the explicit inline-constant dependency claim:
// a public inline const value change invalidates exactly the symbols that
// baked it in, transitively across module boundaries.
func TestInlineConstPropagates(t *testing.T) {
	p := setupProject(t, baseSources())
	p.build(t)
	modified := strings.Replace(basePricing, "pub const BULK_FACTOR: int = 2;", "pub const BULK_FACTOR: int = 3;", 1)
	p.setSource("pricing.rl", modified)
	rep := p.build(t)

	want := []string{
		"pricing.BULK_FACTOR", // definition
		"pricing.bulk_price",  // uses factor directly
		"report.quote",        // uses pricing::BULK_FACTOR across module
		"app.main",            // transitive caller of quote
	}
	assertSetEqual(t, rep.InvalidatedSet, want, "invalidated")
	// unit_price does not touch BULK_FACTOR, so it must survive.
	for _, k := range rep.InvalidatedSet {
		if k == "pricing.unit_price" {
			t.Fatal("unit_price must not be invalidated by BULK_FACTOR change")
		}
	}
	// New concrete behavior, hand-computed: factor 3 =>
	// bulk(10,3)=60*3*3=540, quote = 540+3 = 543.
	p.expectValue(t, "app.main", 3, 543)
}

// TestGenericBodyIsInterfaceDependency records the generic-body contract:
// instantiating callers must recompile when the template body changes.
func TestGenericBodyIsInterfaceDependency(t *testing.T) {
	p := setupProject(t, baseSources())
	p.build(t)
	modified := strings.Replace(baseReport,
		"pub generic fn first(x: T) -> T { return x; }",
		"pub generic fn first(x: T) -> T {\n\t\tif (true == true) { return x; } else { return x; }\n\t}", 1)
	p.setSource("report.rl", modified)
	rep := p.build(t)
	want := []string{"report.first", "report.quote", "app.main"}
	assertSetEqual(t, rep.InvalidatedSet, want, "invalidated")
	// pricing module does not use the generic and must be reusable.
	reusedPricing := false
	for _, m := range rep.ReusedModules {
		if m == "pricing" {
			reusedPricing = true
		}
	}
	if !reusedPricing {
		t.Fatalf("pricing should be reused, changed=%v", rep.ChangedModules)
	}
	p.expectValue(t, "app.main", 3, 362)
}

// TestPublicSignatureChangeRejectsMismatch: widening a public parameter list
// is a hard compile rejection with a typed failure category.
func TestPublicSignatureChangeRejectsMismatch(t *testing.T) {
	p := setupProject(t, baseSources())
	p.build(t)
	modified := strings.Replace(basePricing,
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents); }",
		"pub fn unit_price(cents: int, fee: int) -> int { return private_adjust(cents) + fee; }", 1)
	p.setSource("pricing.rl", modified)
	_, err := build.New(p.cfg, nil).Build()
	if err == nil {
		t.Fatal("expected rejection after signature change breaking callers")
	}
	if !strings.Contains(err.Error(), "arity") {
		t.Fatalf("want arity type error, got: %v", err)
	}
}

// TestSemanticVersionGate tampers the cached semantic version and requires a
// full rebuild plus a stale-version verdict.
func TestSemanticVersionGate(t *testing.T) {
	p := setupProject(t, baseSources())
	p.build(t)
	cachePath := p.cfg.CacheDir + "/cache.json"
	data, err := os.ReadFile(cachePath)
	if err != nil {
		t.Fatal(err)
	}
	tampered := strings.Replace(string(data), `"semantic_version": "rlc-sem-1"`, `"semantic_version": "rlc-sem-0"`, 1)
	if tampered == string(data) {
		t.Fatal("test fixture failed: semantic version marker not found in cache")
	}
	if err := os.WriteFile(cachePath, []byte(tampered), 0o644); err != nil {
		t.Fatal(err)
	}
	rep := p.build(t)
	if !rep.FullRebuild {
		t.Fatal("stale semantic version must force full rebuild")
	}
	if rep.CacheVerdict != "stale_semantic_version" {
		t.Fatalf("verdict = %s", rep.CacheVerdict)
	}
}
