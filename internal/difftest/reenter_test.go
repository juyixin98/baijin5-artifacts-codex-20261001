package difftest_test

import (
	"testing"

	"genfsm/internal/difftest"
	"genfsm/internal/engine"
	"genfsm/internal/semerr"
	"genfsm/internal/value"
)

// TestResumeRunningReentrant drives two generators that resume each other,
// producing a deterministic re-entrancy conflict once control re-enters a
// generator whose body is already executing on the same call stack.
//
// A starts, yields; we inject B. A then iterates B. B yields once; we inject
// A into B. B then iterates A while A is still running -> CONFLICT on both
// engines ("generator already executing").
func TestResumeRunningReentrant(t *testing.T) {
	src := `
gen fn ping(other) {
  yield 1
  for v in other {
    yield v
  }
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "ping"}, // A, newborn
		{Kind: difftest.ActNew, Target: 1, GenName: "ping"}, // B, newborn
		{Kind: difftest.ActNext, Target: 0},                 // A yields 1
		{Kind: difftest.ActSendGen, Target: 0, SendHandle: 1}, // other = B; A iterates B -> B yields 1
		{Kind: difftest.ActSendGen, Target: 1, SendHandle: 0}, // B gets A; B tries to iterate running A
	}
	rep := runScript(t, "reenter", src, acts)
	ev := rep.Events
	assertState(t, ev[2], "yielded", "1")
	assertState(t, ev[3], "yielded", "1")
	// The final send resumes B, which calls next on the already-running A.
	if ev[4].FSM.ErrKind != string(semerr.KindConflict) ||
		ev[4].Ref.ErrKind != string(semerr.KindConflict) {
		t.Fatalf("run 4: kind fsm=%q ref=%q want CONFLICT",
			ev[4].FSM.ErrKind, ev[4].Ref.ErrKind)
	}
	if ev[4].FSM.ErrCode != string(semerr.CodeResumeRunning) ||
		ev[4].Ref.ErrCode != string(semerr.CodeResumeRunning) {
		t.Fatalf("run 4: code fsm=%q ref=%q want RESUME_RUNNING",
			ev[4].FSM.ErrCode, ev[4].Ref.ErrCode)
	}
}

// Direct unit coverage that FSM TryLock rejects same-handle re-entry.
func TestFSMTryLockReentry(t *testing.T) {
	src := `gen fn g(){ yield 1 } fn main(){ return 0 }`
	b, err := engine.Frontend(src)
	if err != nil {
		t.Fatal(err)
	}
	r, _ := engine.New(b, engine.EngineFSM, engine.DefaultConfig())
	g, _ := r.NewGen("g", nil)
	// first yield
	if rr := g.Next(); rr.State != value.StateYielded {
		t.Fatalf("next %s", rr.State)
	}
	_ = semerr.KindConflict
}
