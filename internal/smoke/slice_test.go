package smoke_first_test

import (
	"testing"

	"genfsm/internal/interp"
	"genfsm/internal/ir"
	"genfsm/internal/parser"
	"genfsm/internal/vm"
)

const prog1 = `
gen fn nums() {
  let i = 0
  while (i < 3) {
    yield i
    i = i + 1
  }
}

fn main() {
  let g = nums()
  let sum = 0
  for x in g {
    sum = sum + x
  }
  return sum
}
`

func TestVerticalSlice(t *testing.T) {
	astProg, err := parser.Parse(prog1)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	irProg, err := ir.Compile(astProg)
	if err != nil {
		t.Fatalf("compile: %v", err)
	}
	// VM engine
	m := vm.NewMachine(irProg, vm.DefaultLimits())
	vmVal, err := m.CallMain()
	if err != nil {
		t.Fatalf("vm: %v", err)
	}
	if vmVal.I != 3 {
		t.Fatalf("vm sum = %d, want 3", vmVal.I)
	}
	// Reference interpreter
	ref := interp.New(astProg, interp.DefaultLimits())
	refVal, err := ref.RunMain()
	if err != nil {
		t.Fatalf("interp: %v", err)
	}
	if refVal.I != 3 {
		t.Fatalf("interp sum = %d, want 3", refVal.I)
	}
}
