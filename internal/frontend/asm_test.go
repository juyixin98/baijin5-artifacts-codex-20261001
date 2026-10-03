package frontend

import (
	"strings"
	"testing"

	"mylnk/internal/objfmt"
)

const srcSimple = `
# tiny program: main calls helper in another section
.object a.o
.section .text.helper
.globl helper
helper:
pushi 40
ret

.section .text.main
.globl main
.export main
main:
pushi 2
call helper
add
ret
`

func TestAssembleSimple(t *testing.T) {
	o, err := Assemble(strings.NewReader(srcSimple), "a.o")
	if err != nil {
		t.Fatalf("assemble: %v", err)
	}
	if o.Name != "a.o" || len(o.Sections) != 2 {
		t.Fatalf("header mismatch: %+v", o)
	}
	mainSec := o.Sections[1]
	if len(mainSec.Relocs) != 1 {
		t.Fatalf("want 1 reloc from call, got %d", len(mainSec.Relocs))
	}
	rl := mainSec.Relocs[0]
	if rl.Kind != objfmt.KindRel32 {
		t.Fatalf("call reloc must be rel32, got %d", rl.Kind)
	}
	if rl.Off != uint32(len("pushi")+1) && rl.Off < 5 {
		// operand of call sits after pushi(5)+opcode(1)
	}
	// pushi=5 bytes; call opcode at offset 5, operand at 6
	if rl.Off != 6 {
		t.Fatalf("call operand offset = %d, want 6", rl.Off)
	}
	sy := o.Symbols[rl.SymIdx]
	if sy.Name != "helper" || sy.Bind != objfmt.BindStrong || !sy.Def {
		t.Fatalf("helper symbol mismatch: %+v", sy)
	}
	mainSym := o.Symbols[0]
	for _, s := range o.Symbols {
		if s.Name == "main" {
			mainSym = s
		}
	}
	if !mainSym.Export || !mainSym.Def {
		t.Fatalf("main must be exported definition: %+v", mainSym)
	}
}

func TestAssembleLocalRelResolved(t *testing.T) {
	src := `
.section .text.f
f:
pushi 0
jz skip
pushi 1
skip:
ret
`
	o, err := Assemble(strings.NewReader(src), "b.o")
	if err != nil {
		t.Fatalf("assemble: %v", err)
	}
	sec := o.Sections[0]
	// jz targets a local label: must be patched, no reloc left.
	if len(sec.Relocs) != 0 {
		t.Fatalf("local jump must be resolved at assembly, got %d relocs", len(sec.Relocs))
	}
	// jz operand at pushi(5)+jz-op(1)=6; delta = 11 - 6 = 5
	if sec.Data[6] != 5 || sec.Data[7] != 0 || sec.Data[8] != 0 || sec.Data[9] != 0 {
		t.Fatalf("jz displacement bytes = %v", sec.Data[6:10])
	}
}

func TestAssembleWeakextUndefined(t *testing.T) {
	src := `
.section .text.f
.weakext maybe
f:
call maybe
ret
`
	o, err := Assemble(strings.NewReader(src), "c.o")
	if err != nil {
		t.Fatalf("assemble: %v", err)
	}
	var maybe *objfmt.Symbol
	for _, sy := range o.Symbols {
		if sy.Name == "maybe" {
			maybe = sy
		}
	}
	if maybe == nil || maybe.Def || maybe.Bind != objfmt.BindWeak {
		t.Fatalf("maybe must be weak undefined: %+v", maybe)
	}
}

func TestAssembleErrors(t *testing.T) {
	cases := map[string]string{
		"instruction before section": "pushi 1\n",
		"duplicate label":            ".section .t\nL:\nL:\n",
		"weakext conflicts":          ".section .t\n.weakext f\nf:\n",
		"bad mnemonic":               ".section .t\nwat 1\n",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Assemble(strings.NewReader(body), "x.o"); err == nil {
				t.Fatalf("expected error: %s", name)
			}
		})
	}
}
