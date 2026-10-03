package runtime_test

import (
	"testing"

	"rlmod/internal/fingerprint"
	"rlmod/internal/frontend"
	"rlmod/internal/ir"
	"rlmod/internal/runtime"
)

func build(t *testing.T, mods map[string]string) *ir.Program {
	t.Helper()
	parsed := map[string]*frontend.Program{}
	var order []string
	for name, src := range mods {
		p, err := frontend.Parse(src)
		if err == nil {
			order = append(order, name)
		}
		_ = p
	}
	// parse deterministically via caller-provided map; re-parse in sorted order
	names := make([]string, 0, len(mods))
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

func TestInterpretArithmeticAndBranching(t *testing.T) {
	prog := build(t, map[string]string{
		"app": "package app\nfn Run(): int {\nvar x: int = 10\nif x >= 10 {\nreturn x * 2 + 1\n} else {\nreturn x\n}\n}\n",
	})
	v, err := runtime.New(prog).Run()
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if v.Int != 21 {
		t.Fatalf("got %d want 21", v.Int)
	}
}

func TestInterpretDivisionByZeroCategory(t *testing.T) {
	prog := build(t, map[string]string{
		"app": "package app\nfn Run(): int {\nvar d: int = 0\nreturn 1 / d\n}\n",
	})
	_, err := runtime.New(prog).Run()
	re, ok := err.(*runtime.RuntimeError)
	if !ok {
		t.Fatalf("want RuntimeError, got %T %v", err, err)
	}
	if re.Category != runtime.ErrDivZero {
		t.Fatalf("category = %s want %s", re.Category, runtime.ErrDivZero)
	}
}

func TestInterpretStringConcat(t *testing.T) {
	prog := build(t, map[string]string{
		"app": "package app\nfn Run(): str { return \"a\" + \"b\" }\n",
	})
	v, err := runtime.New(prog).Run()
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !v.IsStr || v.Str != "ab" {
		t.Fatalf("got %+v", v)
	}
}
