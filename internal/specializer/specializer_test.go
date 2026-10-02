package specializer_test

import (
	"io"
	"testing"

	"funcspec/internal/config"
	"funcspec/internal/interp"
	"funcspec/internal/ir"
	"funcspec/internal/observ"
	"funcspec/internal/specializer"
	"funcspec/internal/syntax"
)

func compile(t *testing.T, src string) *ir.Program {
	t.Helper()
	prog, err := syntax.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	irp, err := ir.Lower(prog)
	if err != nil {
		t.Fatalf("lower: %v", err)
	}
	return irp
}

func discardLog() *observ.Logger {
	runID, inID := observ.NewRun("entry", nil, nil)
	return observ.New(io.Discard, runID, inID)
}

func dyn() specializer.Arg          { return specializer.Arg{Static: false} }
func stInt(x int64) specializer.Arg { return specializer.Arg{Static: true, Value: ir.Int(x)} }

func TestPureRecursionFoldsToConstant(t *testing.T) {
	src := `
pure fn fact(n) {
  if (n < 2) { return 1 } else { return n * fact(n - 1) }
}
`
	irp := compile(t, src)
	res, err := specializer.Specialize(irp, "fact", []specializer.Arg{stInt(6)}, config.Default(), discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	if res.StaticResult == nil || res.StaticResult.I != 720 {
		t.Fatalf("static result = %+v, want 720", res.StaticResult)
	}
	if res.Stat.CallsFolded < 6 {
		t.Fatalf("calls folded = %d, want >= 6", res.Stat.CallsFolded)
	}
}

func TestUnknownInputDifferentialExecution(t *testing.T) {
	src := `
pure fn fact(n) {
  if (n < 2) { return 1 } else { return n * fact(n - 1) }
}
`
	irp := compile(t, src)
	res, err := specializer.Specialize(irp, "fact", []specializer.Arg{dyn()}, config.Default(), discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	if res.StaticResult != nil {
		t.Fatal("dynamic entry must not fold")
	}
	for _, n := range []int64{0, 1, 5, 10} {
		got, err := interp.Run(res.Residual, res.Entry, []ir.Value{ir.Int(n)}, interp.DefaultOptions())
		if err != nil {
			t.Fatalf("residual run n=%d: %v", n, err)
		}
		want, err := interp.Run(irp, "fact", []ir.Value{ir.Int(n)}, interp.DefaultOptions())
		if err != nil {
			t.Fatalf("orig run n=%d: %v", n, err)
		}
		if got.Value != want.Value {
			t.Fatalf("n=%d residual=%d orig=%d", n, got.Value, want.Value)
		}
	}
}

func TestEffectsNotPerformedAheadOfTime(t *testing.T) {
	src := `
fn countdown(n) {
  if (n <= 0) { return 0 } else { emit(n); return countdown(n - 1) }
}
`
	irp := compile(t, src)
	res, err := specializer.Specialize(irp, "countdown", []specializer.Arg{stInt(4)}, config.Default(), discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	if res.StaticResult != nil {
		t.Fatal("impure entry folded: side effects would be lost")
	}
	out, err := interp.Run(res.Residual, res.Entry, []ir.Value{}, interp.DefaultOptions())
	if err != nil {
		t.Fatalf("residual run: %v", err)
	}
	if len(out.Effects) != 4 {
		t.Fatalf("effects = %d, want 4", len(out.Effects))
	}
	for i, e := range out.Effects {
		if e.Tag != int64(4-i) {
			t.Fatalf("effect[%d]=%d, want %d", i, e.Tag, 4-i)
		}
	}
}

func TestPolyvariantCacheAndGeneralization(t *testing.T) {
	// down(n) calls itself with a constant offset n-1 plus a dynamic "k".
	// Specializing a fixed entry creates a bounded chain of distinct static
	// patterns; repeated equal patterns must hit the cache, and growth must be
	// stopped by generalization instead of exploding.
	src := `
pure fn down(n, k) {
  if (k <= 0) { return n } else { return down(n - 1, k - 1) }
}
`
	irp := compile(t, src)
	cfg := config.Default()
	cfg.MaxVariantsPerFunc = 5
	res, err := specializer.Specialize(irp, "down",
		[]specializer.Arg{stInt(20), dyn()}, cfg, discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	if res.Stat.VariantsCreated > cfg.MaxVariantsPerFunc+1 {
		t.Fatalf("variants=%d exceeded cap+1 (%d): code growth unbounded",
			res.Stat.VariantsCreated, cfg.MaxVariantsPerFunc+1)
	}
	if res.Stat.Generalized == 0 {
		t.Fatal("expected at least one generalization event once cap was reached")
	}
	// Semantics preserved across dynamic inputs despite bounded variants.
	for _, k := range []int64{0, 1, 4, 5, 6, 30} {
		got, err := interp.Run(res.Residual, res.Entry, []ir.Value{ir.Int(k)}, interp.DefaultOptions())
		if err != nil {
			t.Fatalf("residual k=%d: %v", k, err)
		}
		want, _ := interp.Run(irp, "down", []ir.Value{ir.Int(20), ir.Int(k)}, interp.DefaultOptions())
		if got.Value != want.Value {
			t.Fatalf("k=%d got=%d want=%d", k, got.Value, want.Value)
		}
	}
}

func TestRepeatedPatternsHitCache(t *testing.T) {
	// Two call sites share the same static constant for a callee: only one
	// variant for that pattern may exist.
	src := `
pure fn add1(x) { return x + 1 }
pure fn twice(a, b) { return add1(a) + add1(b) }
`
	irp := compile(t, src)
	res, err := specializer.Specialize(irp, "twice",
		[]specializer.Arg{stInt(10), stInt(10)}, config.Default(), discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	// add1 pattern S10 should be created once then cached.
	if n := countVariantsFor(res.Residual, "add1"); n != 1 {
		t.Fatalf("add1 variants = %d, want 1 (cache reuse)", n)
	}
}

func countVariantsFor(prog *ir.Program, fn string) int {
	n := 0
	prefix := "$" + fn + "__"
	for _, id := range prog.Order {
		if len(id) > len(prefix) && id[:len(prefix)] == prefix {
			n++
		}
	}
	return n
}

func TestGlobalNodeBudgetFallback(t *testing.T) {
	src := `
pure fn grow(n, k) {
  if (k <= 0) { return n } else { return grow(n - 1, k - 1) }
}
`
	irp := compile(t, src)
	cfg := config.Default()
	cfg.MaxResidualNodes = 30 // too small to hold the static chain
	res, err := specializer.Specialize(irp, "grow",
		[]specializer.Arg{stInt(100), dyn()}, cfg, discardLog())
	if err != nil {
		t.Fatalf("non-strict budget must not error, got %v", err)
	}
	if res.Stat.Fallback != "identity" {
		t.Fatalf("fallback = %q, want identity", res.Stat.Fallback)
	}
	for _, k := range []int64{0, 7, 50} {
		got, err := interp.Run(res.Residual, res.Entry,
			[]ir.Value{ir.Int(100), ir.Int(k)}, interp.DefaultOptions())
		if err != nil {
			t.Fatalf("identity residual run: %v", err)
		}
		want, _ := interp.Run(irp, "grow", []ir.Value{ir.Int(100), ir.Int(k)}, interp.DefaultOptions())
		if got.Value != want.Value {
			t.Fatalf("k=%d got=%d want=%d", k, got.Value, want.Value)
		}
	}

	cfg.StrictBudget = true
	if _, err := specializer.Specialize(irp, "grow",
		[]specializer.Arg{stInt(100), dyn()}, cfg, discardLog()); err == nil {
		t.Fatal("strict budget must report E_BUDGET")
	}
}

func TestMixedStaticDynamicCall(t *testing.T) {
	// Pure helper with one known, one unknown argument: cannot be folded, must
	// be specialized with the known arg baked in; impure driver stays at
	// runtime and the whole observable result must match the original.
	src := `
pure fn scale(base, x) { return base * x + 1 }
fn driver(n) {
  if (n <= 0) { return 0 } else {
    emit(scale(3, n))
    return driver(n - 1)
  }
}
`
	irp := compile(t, src)
	res, err := specializer.Specialize(irp, "driver",
		[]specializer.Arg{dyn()}, config.Default(), discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	got, err := interp.Run(res.Residual, res.Entry, []ir.Value{ir.Int(3)}, interp.DefaultOptions())
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	want, _ := interp.Run(irp, "driver", []ir.Value{ir.Int(3)}, interp.DefaultOptions())
	if len(got.Effects) != len(want.Effects) {
		t.Fatalf("effects len %d vs %d", len(got.Effects), len(want.Effects))
	}
	for i := range want.Effects {
		if got.Effects[i].Tag != want.Effects[i].Tag {
			t.Fatalf("effect %d got=%d want=%d", i, got.Effects[i].Tag, want.Effects[i].Tag)
		}
	}
}

func TestDivByZeroResidualized(t *testing.T) {
	// Known denominator zero inside a pure function: the failing op must be
	// residualized, not folded away, so running the residual raises the same
	// E_DIVZERO category as running the original.
	src := `pure fn f(a) { return 10 / (a - a) }`
	irp := compile(t, src)
	res, err := specializer.Specialize(irp, "f",
		[]specializer.Arg{stInt(7)}, config.Default(), discardLog())
	if err != nil {
		t.Fatalf("specialize: %v", err)
	}
	var toRun func() error
	if res.StaticResult != nil {
		t.Fatal("division by zero must not fold to a constant")
	}
	_ = toRun
	_, err = interp.Run(res.Residual, res.Entry, []ir.Value{}, interp.DefaultOptions())
	if err == nil {
		t.Fatal("residual run must fail with division by zero")
	}
}
