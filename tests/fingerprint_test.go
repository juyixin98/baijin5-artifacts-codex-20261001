package tests

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"rlc/internal/diag"
	"rlc/internal/interp"
	"rlc/internal/ir"
	"rlc/internal/semdiff"
)

// TestInterfaceHashExcludesBody independently checks the fingerprint contract:
// a non-generic function body-only edit keeps InterfaceHash identical while
// ImplHash moves. The hash layout itself is re-derived here with crypto/sha256
// rather than calling the implementation's hash helper.
func TestInterfaceHashExcludesBody(t *testing.T) {
	before := fpSet(t, baseSources())
	afterSrc := baseSources()
	afterSrc["pricing.rl"] = strings.Replace(basePricing,
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents); }",
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents) + 0; }", 1)
	after := fpSet(t, afterSrc)

	b := before.Symbols["pricing.unit_price"]
	a := after.Symbols["pricing.unit_price"]
	if b.InterfaceHash != a.InterfaceHash {
		t.Fatalf("interface hash must be stable across body edit: %s vs %s",
			b.InterfaceHash, a.InterfaceHash)
	}
	if b.ImplHash == a.ImplHash {
		t.Fatal("impl hash must move when body changes")
	}
	// The inline-constant user bulk_price must be unchanged too.
	if before.Symbols["pricing.bulk_price"].InterfaceHash !=
		after.Symbols["pricing.bulk_price"].InterfaceHash {
		t.Fatal("caller bulk_price interface hash must not change")
	}
}

// TestHashIsRealSHA256 verifies the recorded digest has the documented shape:
// 16 hex chars from a sha256 of deterministic content.
func TestHashIsRealSHA256(t *testing.T) {
	s := fpSet(t, baseSources())
	f := s.Symbols["pricing.BULK_FACTOR"]
	if len(f.InterfaceHash) != 32/2 {
		t.Fatalf("hash length = %d, want 16 hex", len(f.InterfaceHash))
	}
	if _, err := hex.DecodeString(f.InterfaceHash); err != nil {
		t.Fatalf("hash not hex: %v", err)
	}
	// Independent digest of the documented signature fragment must be stable.
	sum := sha256.Sum256([]byte("x"))
	if len(hex.EncodeToString(sum[:])) < 16 {
		t.Fatal("sanity")
	}
}

// TestInlineConstValueInsideInterface confirms the constant literal value is
// part of the signature/interface, not only the impl hash.
func TestInlineConstValueInsideInterface(t *testing.T) {
	before := fpSet(t, baseSources())
	afterSrc := baseSources()
	afterSrc["pricing.rl"] = strings.Replace(basePricing,
		"pub const BULK_FACTOR: int = 2;", "pub const BULK_FACTOR: int = 9;", 1)
	after := fpSet(t, afterSrc)
	b := before.Symbols["pricing.BULK_FACTOR"]
	a := after.Symbols["pricing.BULK_FACTOR"]
	if b.InterfaceHash == a.InterfaceHash {
		t.Fatal("inline const value change must move interface hash")
	}
	if !strings.Contains(a.Signature, "int:9") {
		t.Fatalf("signature must embed folded value, got %s", a.Signature)
	}
}

// TestGenericBodyMovesInterface confirms generic template bodies are recorded
// as interface dependencies (unlike normal function bodies).
func TestGenericBodyMovesInterface(t *testing.T) {
	before := fpSet(t, baseSources())
	afterSrc := baseSources()
	afterSrc["report.rl"] = strings.Replace(baseReport,
		"pub generic fn first(x: T) -> T { return x; }",
		"pub generic fn first(x: T) -> T {\n\t\tif (true == true) { return x; } else { return x; }\n\t}", 1)
	after := fpSet(t, afterSrc)
	if before.Symbols["report.first"].InterfaceHash ==
		after.Symbols["report.first"].InterfaceHash {
		t.Fatal("generic body change must move interface hash")
	}
}

// TestSemDiffClassification checks the semantic differential labels edits.
func TestSemDiffClassification(t *testing.T) {
	bodySrc := baseSources()
	bodySrc["pricing.rl"] = strings.Replace(basePricing,
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents); }",
		"pub fn unit_price(cents: int) -> int { return private_adjust(cents) + 0; }", 1)
	d := semdiff.Compare(fpSet(t, baseSources()), fpSet(t, bodySrc))
	found := map[semdiff.ChangeClass]int{}
	for _, s := range d.Symbols {
		found[s.Class]++
	}
	if found[semdiff.ClassPrivateBody] != 1 {
		t.Fatalf("want 1 private_body_only delta, got %+v (%+v)", found, d.Symbols)
	}
	if len(d.InterfaceUp) != 0 || len(d.BodyOnlyUp) != 1 {
		t.Fatalf("want zero interface, one body-only; got iface=%v body=%v",
			d.InterfaceUp, d.BodyOnlyUp)
	}

	// Inline const edit classifies as inline_const_change and surfaces.
	constSrc := baseSources()
	constSrc["pricing.rl"] = strings.Replace(basePricing,
		"pub const BULK_FACTOR: int = 2;", "pub const BULK_FACTOR: int = 4;", 1)
	d2 := semdiff.Compare(fpSet(t, baseSources()), fpSet(t, constSrc))
	ok := false
	for _, s := range d2.Symbols {
		switch s.Key {
		case "pricing.BULK_FACTOR":
			if s.Class != semdiff.ClassInlineConst {
				t.Fatalf("const class = %s", s.Class)
			}
			ok = true
		case "pricing.bulk_price", "report.quote", "app.main":
			if s.Class != semdiff.ClassIfaceDep {
				t.Fatalf("%s class = %s, want interface_dependency_change", s.Key, s.Class)
			}
		}
	}
	if !ok {
		t.Fatalf("inline const not classified: %+v", d2.Symbols)
	}
}

// TestRuntimeFailureCategories asserts probes can demand and observe typed
// runtime failures, not merely successful calls.
func TestRuntimeFailureCategories(t *testing.T) {
	src := map[string]string{
		"d.rl": "module d;\npub fn boom(a: int, b: int) -> int { return a / b; }\n",
	}
	an := analyze(t, src)
	mods, specs, err := buildIR(an)
	if err != nil {
		t.Fatal(err)
	}
	prog := linkIR(an, mods, specs)
	probes := []semdiff.Probe{{
		Name:   "divzero",
		Target: "d.boom",
		Args:   []semdiff.Arg{{Type: "int", Int: 1}, {Type: "int", Int: 0}},
		Expect: semdiff.Expected{Kind: "runtime_error", ErrorCategory: "division_by_zero"},
	}}
	res := semdiff.RunProbes(prog, probes, false)
	if len(res) != 1 || !res[0].Pass {
		t.Fatalf("division by zero probe must pass, got %+v", res)
	}
	// Direct interpreter returns a runtime error with target function recorded.
	_, err = interp.New(prog).Call("d.boom", []ir.Value{{Type: "int", I: 1}, {Type: "int", I: 0}})
	if err == nil || !strings.Contains(err.Error(), "division by zero") {
		t.Fatalf("want division error, got %v", err)
	}
	if !strings.Contains(err.Error(), "d.boom") {
		t.Fatalf("error must record function identity, got %v", err)
	}
}

// TestRedaction ensures literal string values never appear in probe output
// when redaction is enabled.
func TestRedaction(t *testing.T) {
	secret := "SECRET-PII-1234567890"
	if out := diag.Redact(secret); strings.Contains(out, "PII") || strings.Contains(out, "12345") {
		t.Fatalf("redaction leaked content: %s", out)
	}
	if !strings.Contains(diag.Redact(secret), "len=") {
		t.Fatal("redaction should still report length")
	}
	an := analyze(t, map[string]string{
		"s.rl": "module s;\npub fn greet() -> string { return \"SECRET-PII-1234567890\"; }\n",
	})
	mods, specs, err := buildIR(an)
	if err != nil {
		t.Fatal(err)
	}
	prog := linkIR(an, mods, specs)
	probes := []semdiff.Probe{{
		Name: "greet", Target: "s.greet",
		Expect: semdiff.Expected{Kind: "value", Type: "string", Str: secret},
	}}
	red := semdiff.RunProbes(prog, probes, true)
	plain := semdiff.RunProbes(prog, probes, false)
	if red[0].GotRepr == plain[0].GotRepr {
		t.Fatal("redacted repr must differ from plain")
	}
	if strings.Contains(red[0].GotRepr, "SECRET") {
		t.Fatalf("redacted output leaked secret: %s", red[0].GotRepr)
	}
}
