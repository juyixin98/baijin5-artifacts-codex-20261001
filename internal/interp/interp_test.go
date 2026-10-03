package interp

import (
	"strings"
	"testing"

	"rlc/internal/ir"
)

func fn(name, result string, body []ir.Instr, params ...string) *ir.Func {
	return &ir.Func{Name: name, Short: name, Params: params, ParamTypes: params, Result: result, Body: body, NumLocals: len(params)}
}

func TestArithmeticAndControlFlow(t *testing.T) {
	// max(a,b): if a < b return b else return a
	body := []ir.Instr{
		{Op: ir.OpLoad, Slot: 0}, {Op: ir.OpLoad, Slot: 1}, {Op: ir.OpBin, Bin: ir.BinLt},
		{Op: ir.OpJumpIfFalse, Jump: 7},
		{Op: ir.OpLoad, Slot: 1}, {Op: ir.OpReturn},
		{Op: ir.OpJump, Jump: 9},
		{Op: ir.OpLoad, Slot: 0}, {Op: ir.OpReturn}, // indices 7,8
	}
	prog := &ir.Program{Modules: []string{"m"}, Funcs: []*ir.Func{fn("m.max", "int", body, "int", "int")}}
	e := New(prog)
	cases := [][3]int64{{3, 5, 5}, {9, 2, 9}, {7, 7, 7}}
	for _, c := range cases {
		v, err := e.Call("m.max", []ir.Value{{Type: "int", I: c[0]}, {Type: "int", I: c[1]}})
		if err != nil {
			t.Fatal(err)
		}
		if v.I != c[2] {
			t.Errorf("max(%d,%d)=%d want %d", c[0], c[1], v.I, c[2])
		}
	}
}

func TestStringConcatAndEquality(t *testing.T) {
	// eq(a,b) returning bool then tern-like
	body := []ir.Instr{
		{Op: ir.OpLoad, Slot: 0}, {Op: ir.OpLoad, Slot: 1}, {Op: ir.OpBin, Bin: ir.BinEq},
		{Op: ir.OpJumpIfFalse, Jump: 7},
		{Op: ir.OpConstStr, Str: "yes"}, {Op: ir.OpReturn},
		{Op: ir.OpJump, Jump: 9},
		{Op: ir.OpConstStr, Str: "no"}, {Op: ir.OpReturn},
	}
	prog := &ir.Program{Funcs: []*ir.Func{fn("m.cmp", "string", body, "string", "string")}}
	e := New(prog)
	v, err := e.Call("m.cmp", []ir.Value{{Type: "string", S: "a"}, {Type: "string", S: "a"}})
	if err != nil || v.S != "yes" {
		t.Fatalf("got %v %v", v, err)
	}
	v, _ = e.Call("m.cmp", []ir.Value{{Type: "string", S: "a"}, {Type: "string", S: "b"}})
	if v.S != "no" {
		t.Fatalf("got %q", v.S)
	}

	// string + string
	cat := []ir.Instr{{Op: ir.OpLoad, Slot: 0}, {Op: ir.OpLoad, Slot: 1}, {Op: ir.OpBin, Bin: ir.BinAdd}, {Op: ir.OpReturn}}
	prog2 := &ir.Program{Funcs: []*ir.Func{fn("m.cat", "string", cat, "string", "string")}}
	v, err = New(prog2).Call("m.cat", []ir.Value{{Type: "string", S: "foo"}, {Type: "string", S: "bar"}})
	if err != nil || v.S != "foobar" {
		t.Fatalf("concat got %q %v", v.S, err)
	}
}

func TestRuntimeErrors(t *testing.T) {
	div := []ir.Instr{{Op: ir.OpLoad, Slot: 0}, {Op: ir.OpLoad, Slot: 1}, {Op: ir.OpBin, Bin: ir.BinDiv}, {Op: ir.OpReturn}}
	prog := &ir.Program{Funcs: []*ir.Func{fn("m.div", "int", div, "int", "int")}}
	e := New(prog)
	if _, err := e.Call("m.div", []ir.Value{{Type: "int", I: 1}, {Type: "int", I: 0}}); err == nil ||
		!strings.Contains(err.Error(), "division by zero") {
		t.Fatalf("want division error, got %v", err)
	}
	if _, err := e.Call("m.missing", nil); err == nil {
		t.Fatal("want undefined function error")
	}
}

func TestProgramCodecRoundtrip(t *testing.T) {
	div := []ir.Instr{{Op: ir.OpLoad, Slot: 0}, {Op: ir.OpLoad, Slot: 1}, {Op: ir.OpBin, Bin: ir.BinDiv}, {Op: ir.OpReturn}}
	prog := &ir.Program{Modules: []string{"m"}, Funcs: []*ir.Func{fn("m.div", "int", div, "int", "int")},
		Consts: []*ir.Const{{Name: "m.K", Module: "m", Short: "K", Pub: true, Type: "int", Value: ir.Value{Type: "int", I: 7}}}}
	data, err := ir.MarshalProgram(prog)
	if err != nil {
		t.Fatal(err)
	}
	back, err := ir.UnmarshalProgram(data)
	if err != nil {
		t.Fatal(err)
	}
	v, err := New(back).Call("m.div", []ir.Value{{Type: "int", I: 10}, {Type: "int", I: 2}})
	if err != nil || v.I != 5 {
		t.Fatalf("roundtrip result %v err=%v", v, err)
	}
	if c := back.Const_("m.K"); c == nil || c.Value.I != 7 {
		t.Fatal("const did not roundtrip")
	}
}
