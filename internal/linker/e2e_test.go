package linker_test

import (
	"strings"
	"testing"

	"mylnk/internal/frontend"
	"mylnk/internal/ir"
	"mylnk/internal/linker"
	"mylnk/internal/objfmt"
	"mylnk/internal/runtime"
)

func asm(t *testing.T, name, src string) *objfmt.Object {
	t.Helper()
	o, err := frontend.Assemble(strings.NewReader(src), name)
	if err != nil {
		t.Fatalf("assemble %s: %v", name, err)
	}
	return o
}

func linkRun(t *testing.T, objs []*objfmt.Object, entry string) (int32, *ir.ReachResult) {
	t.Helper()
	p, err := ir.Build(objs, entry)
	if err != nil {
		t.Fatalf("ir build: %v", err)
	}
	rr := p.AnalyzeReach()
	if errs := p.UndefinedDiagnostics(rr); len(errs) != 0 {
		t.Fatalf("undefined symbols: %v", errs)
	}
	img, err := linker.Link(p, rr)
	if err != nil {
		t.Fatalf("link: %v", err)
	}
	res, err := runtime.Run(img, 0, false)
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	return res.Return, rr
}

// Weak definition "provider" returns 2; strong provider returns 40.
// main pushes 2 then calls provider and adds: weak->4, strong->42.
func TestWeakStrongOverrideE2E(t *testing.T) {
	main := `
.section .text.main
.globl main
.export main
main:
pushi 2
call provider
add
ret
`
	weakProvider := `
.section .text.provider
.weak provider
provider:
pushi 2
ret
`
	strongProvider := `
.section .text.provider
.globl provider
provider:
pushi 40
ret
`
	// Case 1: only weak definition present.
	rv, _ := linkRun(t, []*objfmt.Object{
		asm(t, "main.o", main), asm(t, "weak.o", weakProvider),
	}, "main")
	if rv != 4 {
		t.Fatalf("weak-only: return = %d, want 4", rv)
	}

	// Case 2: strong object added must override weak; weak section dropped.
	objs := []*objfmt.Object{
		asm(t, "main.o", main), asm(t, "weak.o", weakProvider), asm(t, "strong.o", strongProvider),
	}
	p, err := ir.Build(objs, "main")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	if errs := p.UndefinedDiagnostics(rr); len(errs) != 0 {
		t.Fatalf("undefined: %v", errs)
	}
	var droppedWeak bool
	for _, d := range rr.Dropped {
		if d.Obj.Name == "weak.o" {
			droppedWeak = true
		}
	}
	if !droppedWeak {
		t.Fatal("weak provider section must be reclaimed when strong overrides")
	}
	img, err := linker.Link(p, rr)
	if err != nil {
		t.Fatalf("link: %v", err)
	}
	res, err := runtime.Run(img, 0, false)
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Return != 42 {
		t.Fatalf("strong-override: return = %d, want 42", res.Return)
	}
}

// Mutual recursion across two objects/sections:
// iseven(n): n==0 -> 1 ; else isodd(n-1)
// isodd(n):  n==0 -> 0 ; else iseven(n-1)
func TestCircularCrossObjectE2E(t *testing.T) {
	even := `
.section .text.iseven
.globl iseven
.export iseven
iseven:
jz iseven_zero
pushi 1
sub
call isodd
ret
iseven_zero:
pop
pushi 1
halt
`
	odd := `
.section .text.isodd
.globl isodd
isodd:
jz isodd_zero
pushi 1
sub
call iseven
ret
isodd_zero:
pop
pushi 0
halt
`
	// iseven(10) = 1 ; iseven(7) = 0
	for _, tc := range []struct {
		n    int32
		want int32
	}{{10, 1}, {7, 0}, {0, 1}, {1, 0}} {
		main := "pushi " + itoa(int(tc.n)) + "\ncall iseven\nret\n"
		rv, rr := linkRun(t, []*objfmt.Object{
			asm(t, "main.o", ".section .text.main\n.globl main\n.export main\nmain:\n"+main),
			asm(t, "even.o", even),
			asm(t, "odd.o", odd),
		}, "main")
		if rv != tc.want {
			t.Fatalf("iseven(%d)=%d want %d", tc.n, rv, tc.want)
		}
		if len(rr.Dropped) != 0 {
			t.Fatalf("mutually recursive sections must both be kept, dropped=%d", len(rr.Dropped))
		}
	}
}

func TestUndefinedSymbolE2E(t *testing.T) {
	main := `
.section .text.main
.globl main
.export main
main:
call ghost
ret
`
	objs := []*objfmt.Object{asm(t, "main.o", main)}
	p, err := ir.Build(objs, "main")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	errs := p.UndefinedDiagnostics(rr)
	if len(errs) != 1 || errs[0].Kind != ir.KindUndefinedSymbol {
		t.Fatalf("want undefined-symbol diagnostic, got %v", errs)
	}
	if !strings.Contains(errs[0].Error(), "ghost") {
		t.Fatalf("diagnostic must name ghost: %v", errs[0])
	}
}

func TestMultipleStrongE2E(t *testing.T) {
	a := ".section .t\n.globl x\nx:\npushi 1\nret\n"
	_, err := ir.Build([]*objfmt.Object{asm(t, "a.o", a), asm(t, "b.o", a)}, "x")
	if err == nil {
		t.Fatal("expected multiple strong definition error")
	}
	le := err.(*ir.LinkError)
	if le.Kind != ir.KindMultipleStrong {
		t.Fatalf("want %s, got %s", ir.KindMultipleStrong, le.Kind)
	}
}

func TestWeakNullCallRuntimeError(t *testing.T) {
	main := `
.section .text.main
.weakext maybe
.globl main
.export main
main:
call maybe
ret
`
	p, err := ir.Build([]*objfmt.Object{asm(t, "main.o", main)}, "main")
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	rr := p.AnalyzeReach()
	if errs := p.UndefinedDiagnostics(rr); len(errs) != 0 {
		t.Fatalf("weak undefined must link: %v", errs)
	}
	img, err := linker.Link(p, rr)
	if err != nil {
		t.Fatalf("link: %v", err)
	}
	// call rel32 against null target => target 0, pc at operand... reloc
	// produces delta to address 0; VM then executes at a low address =>
	// either illegal opcode (below image) or explicit failure, never success.
	if _, err := runtime.Run(img, 0, false); err == nil {
		t.Fatal("calling null weak symbol must fail at runtime")
	} else if le := err.(*ir.LinkError); le.Kind != ir.KindRuntime {
		t.Fatalf("want runtime error category, got %s", le.Kind)
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
}
