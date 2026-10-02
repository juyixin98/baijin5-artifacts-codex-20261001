package interp_test

import (
	"testing"

	"funcspec/internal/interp"
	"funcspec/internal/ir"
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

func TestFactorialEndToEnd(t *testing.T) {
	src := `
pure fn fact(n) {
  if (n < 2) { return 1 } else { return n * fact(n - 1) }
}
`
	irp := compile(t, src)
	res, err := interp.Run(irp, "fact", []ir.Value{ir.Int(5)}, interp.DefaultOptions())
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Value != 120 {
		t.Fatalf("fact(5) = %d, want 120", res.Value)
	}
}

func TestEmitEffectsOrdering(t *testing.T) {
	src := `
fn loop(n) {
  if (n <= 0) {
    return 0
  } else {
    emit(n)
    return loop(n - 1)
  }
}
`
	irp := compile(t, src)
	res, err := interp.Run(irp, "loop", []ir.Value{ir.Int(3)}, interp.DefaultOptions())
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Value != 0 {
		t.Fatalf("loop(3) = %d, want 0", res.Value)
	}
	want := []int64{3, 2, 1}
	if len(res.Effects) != len(want) {
		t.Fatalf("effects = %v, want %v", res.Effects, want)
	}
	for i, e := range res.Effects {
		if e.Tag != want[i] || e.Func != "loop" {
			t.Fatalf("effect[%d] = %+v, want tag %d/func loop", i, e, want[i])
		}
	}
}

func TestDivisionByZeroCategory(t *testing.T) {
	src := `pure fn f(a) { return a / 0 }`
	irp := compile(t, src)
	_, err := interp.Run(irp, "f", []ir.Value{ir.Int(1)}, interp.DefaultOptions())
	if err == nil {
		t.Fatal("want division by zero error")
	}
}
