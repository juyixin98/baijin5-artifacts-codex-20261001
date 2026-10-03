package ir_test

import (
	"strings"
	"testing"

	"rlmod/internal/fingerprint"
	"rlmod/internal/frontend"
	"rlmod/internal/ir"
)

func buildOK(t *testing.T, mods map[string]string) *ir.Program {
	t.Helper()
	names := make([]string, 0, len(mods))
	parsed := map[string]*frontend.Program{}
	for n := range mods {
		names = append(names, n)
	}
	for _, n := range names {
		p, err := frontend.Parse(mods[n])
		if err != nil {
			t.Fatalf("parse %s: %v", n, err)
		}
		parsed[n] = p
	}
	prog, err := ir.Build(parsed, names, "1.0.0", fingerprint.SchemaVersion)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	return prog
}

func buildErr(t *testing.T, mods map[string]string) *ir.CompileError {
	t.Helper()
	names := make([]string, 0, len(mods))
	parsed := map[string]*frontend.Program{}
	for n := range mods {
		names = append(names, n)
	}
	for _, n := range names {
		p, _ := frontend.Parse(mods[n])
		parsed[n] = p
	}
	_, err := ir.Build(parsed, names, "1.0.0", fingerprint.SchemaVersion)
	if err == nil {
		t.Fatal("expected compile error")
	}
	ce, ok := err.(*ir.CompileError)
	if !ok {
		t.Fatalf("want CompileError, got %T: %v", err, err)
	}
	return ce
}

func TestConstFoldingCrossModule(t *testing.T) {
	prog := buildOK(t, map[string]string{
		"base": "package base\nconst A int = 40\nconst B int = A + 2\n",
		"app":  "package app\nimport base\nconst C int = base::B + 1\nfn V(): int { return C }\n",
	})
	c := prog.Modules["app"].Consts["C"]
	if c.IntVal != 43 {
		t.Fatalf("folded C = %d want 43", c.IntVal)
	}
}

func TestConstCycleRejected(t *testing.T) {
	ce := buildErr(t, map[string]string{
		"m": "package m\nconst A int = B\nconst B int = A\n",
	})
	if ce.Kind != ir.KindConstCycle {
		t.Fatalf("kind = %s want %s", ce.Kind, ir.KindConstCycle)
	}
}

func TestPrivateCrossModuleRejected(t *testing.T) {
	ce := buildErr(t, map[string]string{
		"lib": "package lib\nfn hidden(): int { return 1 }\n",
		"app": "package app\nimport lib\nfn Run(): int { return lib::hidden() }\n",
	})
	if ce.Kind != ir.KindUnknownSymbol {
		t.Fatalf("kind = %s want unknown_symbol", ce.Kind)
	}
	if !strings.Contains(ce.Error(), "private") {
		t.Fatalf("error should mention private: %v", ce)
	}
}

func TestArityMismatch(t *testing.T) {
	ce := buildErr(t, map[string]string{
		"app": "package app\nfn Add(a: int, b: int): int { return a + b }\nfn Run(): int { return Add(1) }\n",
	})
	if ce.Kind != ir.KindArityMismatch {
		t.Fatalf("kind = %s", ce.Kind)
	}
}

func TestTypeMismatch(t *testing.T) {
	ce := buildErr(t, map[string]string{
		"app": "package app\nfn Run(): int { var s: str = \"x\"\nreturn s }\n",
	})
	if ce.Kind != ir.KindTypeMismatch {
		t.Fatalf("kind = %s", ce.Kind)
	}
}

func TestUnknownModuleImport(t *testing.T) {
	ce := buildErr(t, map[string]string{
		"app": "package app\nimport ghost\nfn Run(): int { return 1 }\n",
	})
	if ce.Kind != ir.KindImportMissing {
		t.Fatalf("kind = %s", ce.Kind)
	}
}

func TestGenericMonomorphization(t *testing.T) {
	prog := buildOK(t, map[string]string{
		"m": "package m\nfn Pair[T](a: T, b: T): T { return a + b }\nfn Run(): int { return Pair[int](20, 22) }\n",
	})
	if _, ok := prog.Modules["m"].Funcs["Pair[int]"]; !ok {
		t.Fatalf("Pair[int] not monomorphized: %v", funcKeys(prog))
	}
}

func TestGenericWrongTypeArgCount(t *testing.T) {
	ce := buildErr(t, map[string]string{
		"m": "package m\nfn Pair[T](a: T, b: T): T { return a + b }\nfn Run(): int { return Pair[int, str](1, 2) }\n",
	})
	if ce.Kind != ir.KindTypeArgCount {
		t.Fatalf("kind = %s", ce.Kind)
	}
}

func funcKeys(prog *ir.Program) []string {
	var out []string
	for _, f := range prog.Modules["m"].Funcs {
		out = append(out, f.Key)
	}
	return out
}
