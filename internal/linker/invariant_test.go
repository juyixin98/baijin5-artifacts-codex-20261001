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

func asmObjs(t *testing.T, items ...[2]string) []*objfmt.Object {
	t.Helper()
	var objs []*objfmt.Object
	for _, x := range items {
		o, err := frontend.Assemble(strings.NewReader(x[1]), x[0])
		if err != nil {
			t.Fatalf("asm %s: %v", x[0], err)
		}
		objs = append(objs, o)
	}
	return objs
}

// Dropped sections never occupy image space and every applied reloc target
// (from a kept section) lies within the image and runs successfully.
func TestNoDanglingRelocationInvariant(t *testing.T) {
	main := `.section .text.main
.globl main
.export main
main:
call helper
halt
`
	helper := `.section .text.helper
.globl helper
helper:
pushi 5
ret
`
	objs := asmObjs(t, [2]string{"m.o", main}, [2]string{"h.o", helper})
	p, err := ir.Build(objs, "main")
	if err != nil {
		t.Fatal(err)
	}
	rr := p.AnalyzeReach()
	img, err := linker.Link(p, rr)
	if err != nil {
		t.Fatalf("link: %v", err)
	}
	for _, d := range rr.Dropped {
		for _, ps := range img.Sections {
			if ps.Sec == d {
				t.Fatalf("dropped section %s:%s placed in image", d.Obj.Name, d.Name)
			}
		}
	}
	res, err := runtime.Run(img, 0, false)
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if res.Return != 5 {
		t.Fatalf("return = %d want 5", res.Return)
	}
}

// Tampering with reachability so a kept section references a dropped one
// must produce an internal-invariant failure, never a dangling relocation.
func TestDanglingLocalRelocRejected(t *testing.T) {
	main := `.section .text.main
.globl main
.export main
main:
call helper
halt
`
	helper := `.section .text.helper
.globl helper
helper:
pushi 5
ret
`
	objs := asmObjs(t, [2]string{"m.o", main}, [2]string{"h.o", helper})
	p, err := ir.Build(objs, "main")
	if err != nil {
		t.Fatal(err)
	}
	rr := p.AnalyzeReach()
	for id := range rr.Kept {
		if p.Sections[id].Name == ".text.helper" {
			delete(rr.Kept, id)
		}
	}
	_, err = linker.Link(p, rr)
	if err == nil {
		t.Fatal("expected error for dangling relocation")
	}
	le := err.(*ir.LinkError)
	if le.Kind != ir.KindInternalInvariant && le.Kind != ir.KindUndefinedSymbol {
		t.Fatalf("want invariant/undefined category, got %s", le.Kind)
	}
}

// Infinite recursion (no termination) must be bounded and classified.
func TestRuntimeStepLimit(t *testing.T) {
	loop := `.section .text.loop
.globl main
.export main
main:
pushi 1
call main
`
	objs := asmObjs(t, [2]string{"loop.o", loop})
	p, err := ir.Build(objs, "main")
	if err != nil {
		t.Fatal(err)
	}
	rr := p.AnalyzeReach()
	img, err := linker.Link(p, rr)
	if err != nil {
		t.Fatal(err)
	}
	_, err = runtime.Run(img, 100, false)
	if err == nil {
		t.Fatal("expected step-limit runtime error")
	}
	if le := err.(*ir.LinkError); le.Kind != ir.KindRuntime {
		t.Fatalf("want runtime category, got %s", le.Kind)
	}
}

// Data word pointing at an address the VM jumps into as code may hit an
// illegal opcode; assert explicit classification instead of silent success.
func TestIllegalOpcodeClassified(t *testing.T) {
	bad := `.section .text.main
.globl main
.export main
main:
pushi 1
halt
.section .text.trap
.word 0x77
`
	objs := asmObjs(t, [2]string{"bad.o", bad})
	p, err := ir.Build(objs, "main")
	if err != nil {
		t.Fatal(err)
	}
	rr := p.AnalyzeReach()
	img, err := linker.Link(p, rr)
	if err != nil {
		t.Fatal(err)
	}
	// Manually enter the trap address; normal run ends at halt with 1.
	res, err := runtime.Run(img, 0, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Return != 1 {
		t.Fatalf("normal run return = %d want 1", res.Return)
	}
	// Locate .text.trap and run from it directly to exercise illegal opcode.
	var trap uint32
	for _, s := range img.Sections {
		if s.Sec.Name == ".text.trap" {
			trap = s.Addr
		}
	}
	img.EntryPC = trap
	if _, err := runtime.Run(img, 0, false); err == nil {
		t.Fatal("illegal opcode must be a runtime error")
	}
}
