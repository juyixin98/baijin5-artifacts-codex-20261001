package interp_test

import (
	"testing"

	"funcspect/internal/front"
	"funcspect/internal/interp"
	"funcspect/internal/ir"
)

const sliceSrc = `
pure fact(n) = if n <= 1 then 1 else n * fact(n - 1)
main() = fact(5)
`

func buildForTest(t *testing.T, src string) *ir.Program {
	t.Helper()
	astP, err := front.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	prog, err := ir.Build(astP)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	return prog
}

func TestVerticalSliceFactorial(t *testing.T) {
	prog := buildForTest(t, sliceSrc)
	res, err := interp.Run(prog, nil, nil, interp.DefaultLimits())
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Value != 120 {
		t.Fatalf("fact(5) = %d, want 120", res.Value)
	}
	if res.Trace.Steps == 0 {
		t.Fatalf("expected non-zero step count")
	}
}
