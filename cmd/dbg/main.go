package main

import (
	"fmt"

	"genfsm/internal/ir"
	"genfsm/internal/parser"
)

func main() {
	b, _ := parser.Parse(`
gen fn ping(other) {
  yield 1
  for v in other {
    yield v
  }
}
fn main() { return 0 }
`)
	p, _ := ir.Compile(b)
	r := p.Routines["ping"]
	names := []string{"NOP","NULL","BOOL","INT","STR","LOAD","STORE","POP","UN","BIN","CALL","JMP","BF","BT","RET","THROW","YIELD","SETUP","POPH","BIND","EF","EFU","ENDF","SITER","PITER","INEXT"}
	for i, op := range r.Code {
		n := ""; if int(op.C)<len(names){n=names[op.C]}
		fmt.Printf("%3d %-7s slot=%d j1=%d\n", i, n, op.I, op.J1)
	}
}
