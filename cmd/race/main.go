package main

import (
	"fmt"

	"genfsm/internal/engine"
	"genfsm/internal/value"
)

func main() {
	src := `
gen fn ping() {
  let other = yield 0
  yield 1
  for v in other {
    yield v
  }
}
fn main() { return 0 }
`
	b, _ := engine.Frontend(src)
	r, _ := engine.New(b, engine.EngineFSM, engine.DefaultConfig())
	A, _ := r.NewGen("ping", nil)
	B, _ := r.NewGen("ping", nil)
	show := func(tag string, rr value.Result) {
		fmt.Printf("%s state=%s val=%v kind=%s code=%s ex=%s:%s\n",
			tag, rr.State, rr.Value.Display(), rr.ErrKind, rr.ErrCode, rr.ExName, rr.ExMessage)
	}
	show("A.next", A.Next()) // yields 0
	show("B.next", B.Next()) // yields 0
	show("A.send(B)", A.Send(value.GenV(B))) // other=B, yields 1
	show("B.send(A)", B.Send(value.GenV(A))) // other=A, yields 1
	// Now advance both into each other's for-loop:
	show("A.next", A.Next()) // A iterates B: B suspended -> A yields B's next
	show("B.next", B.Next()) // B iterates A while A running -> conflict

}
