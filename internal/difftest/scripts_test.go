package difftest_test

import (
	"os"
	"path/filepath"
	"testing"

	"genfsm/internal/difftest"
	"genfsm/internal/engine"
	"genfsm/internal/value"
)

// logFile returns the per-test JSONL replay log path under logs/.
func logFile(t *testing.T) string {
	t.Helper()
	dir := filepath.Join("..", "..", "logs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(dir, "difftest-"+t.Name()+".jsonl")
}

func runScript(t *testing.T, name, src string, acts []difftest.Action) *difftest.Report {
	t.Helper()
	path := logFile(t)
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	lg := difftest.NewLogger(f)
	lg.Header(t.Name())
	rep, err := difftest.Run(name, src, acts, engine.DefaultConfig())
	if err != nil {
		lg.Note("frontend_error", err.Error())
		t.Fatalf("%s: %v", name, err)
	}
	lg.Script(rep)
	lg.Report(rep)
	if !rep.Passed {
		t.Errorf("%s: %s (log %s)", name, rep.FirstErr, path)
	}
	return rep
}

func intv(i int64) value.Value  { return value.IntV(i) }
func strv(s string) value.Value { return value.StrV(s) }

// Basic yield/next across a loop, including exhaustion.
func TestBasicIteration(t *testing.T) {
	src := `
gen fn nums(n) {
  let i = 0
  while (i < n) {
    yield i
    i = i + 1
  }
  return 42
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "nums", Args: []value.Value{intv(3)}},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActNext, Target: 0}, // finished with return value 42
		{Kind: difftest.ActNext, Target: 0}, // exhausted
	}
	rep := runScript(t, "basic", src, acts)
	ev := rep.Events
	assertState(t, ev[1], "yielded", "0")
	assertState(t, ev[2], "yielded", "1")
	assertState(t, ev[3], "yielded", "2")
	assertState(t, ev[4], "finished", "42")
	assertCode(t, ev[5], "finished", "STOP_ITERATION")
}

// send delivers values at yield sites; send into newborn is a conflict.
func TestSendValues(t *testing.T) {
	src := `
gen fn echo() {
  let a = yield 1
  let b = yield a
  yield b
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "echo"},
		{Kind: difftest.ActSend, Target: 0, SendVal: intv(7), Comment: "send newborn"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActSend, Target: 0, SendVal: intv(7)},
		{Kind: difftest.ActSend, Target: 0, SendVal: intv(8)},
		{Kind: difftest.ActNext, Target: 0},
	}
	rep := runScript(t, "send", src, acts)
	ev := rep.Events
	assertCode(t, ev[1], "error", "SEND_NEWBORN")
	assertKindCode(t, ev[1], "CONFLICT", "SEND_NEWBORN")
	assertState(t, ev[2], "yielded", "1")
	assertState(t, ev[3], "yielded", "7")
	assertState(t, ev[4], "yielded", "8")
	assertState(t, ev[5], "finished", "null")
}

// throw injects an exception at the yield site; catch handles it.
func TestThrowInjection(t *testing.T) {
	src := `
gen fn guarded() {
  try {
    yield 1
    yield 2
  } catch (e) {
    yield 3
  }
  yield 4
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "guarded"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActThrow, Target: 0, ExName: "Boom", ExMsg: "injected"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActNext, Target: 0},
	}
	rep := runScript(t, "throw", src, acts)
	ev := rep.Events
	assertState(t, ev[1], "yielded", "1")
	assertState(t, ev[2], "yielded", "3")
	assertState(t, ev[3], "yielded", "4")
	assertState(t, ev[4], "finished", "null")
}

// Uncaught injected exception terminates the generator with that exception.
func TestUncaughtThrow(t *testing.T) {
	src := `
gen fn plain() {
  yield 1
  yield 2
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "plain"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActThrow, Target: 0, ExName: "Kaboom", ExMsg: "x"},
	}
	rep := runScript(t, "uncaught", src, acts)
	ev := rep.Events
	assertEx(t, ev[2], "Kaboom", "x")
}

// close runs finally exactly once; repeat close is a no-op.
func TestCloseFinallyOnce(t *testing.T) {
	src := `
gen fn fin() {
  try {
    yield 1
    yield 2
    yield 3
  } finally {
  }
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "fin"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActClose, Target: 0},
		{Kind: difftest.ActClose, Target: 0},
		{Kind: difftest.ActStatus, Target: 0},
		{Kind: difftest.ActNext, Target: 0},
	}
	rep := runScript(t, "close_once", src, acts)
	ev := rep.Events
	assertState(t, ev[1], "yielded", "1")
	assertState(t, ev[2], "closed", "")
	assertState(t, ev[3], "closed", "")
	if got := ev[4].FSM.Status; got != "closed" || ev[4].Ref.Status != "closed" {
		t.Fatalf("status after close = %s/%s", ev[4].FSM.Status, ev[4].Ref.Status)
	}
	assertCode(t, ev[5], "finished", "STOP_ITERATION")
}

// close on a newborn applies no cleanup and is idempotent.
func TestCloseNewborn(t *testing.T) {
	src := `
gen fn late() {
  yield 1
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "late"},
		{Kind: difftest.ActClose, Target: 0},
		{Kind: difftest.ActClose, Target: 0},
		{Kind: difftest.ActNext, Target: 0},
	}
	rep := runScript(t, "close_newborn", src, acts)
	ev := rep.Events
	assertState(t, ev[1], "closed", "")
	assertState(t, ev[2], "closed", "")
	assertCode(t, ev[3], "finished", "STOP_ITERATION")
}

// Nested cleanup: closing outer closes the inner for-loop generator too.
func TestNestedCleanup(t *testing.T) {
	src := `
gen fn inner() {
  try {
    yield 10
    yield 20
    yield 30
  } finally {
  }
}
gen fn outer() {
  try {
    for v in inner() {
      yield v
    }
  } finally {
  }
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "outer"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActClose, Target: 0},
		{Kind: difftest.ActClose, Target: 0},
	}
	rep := runScript(t, "nested_cleanup", src, acts)
	ev := rep.Events
	assertState(t, ev[1], "yielded", "10")
	assertState(t, ev[2], "closed", "")
	assertState(t, ev[3], "closed", "")
}

// Yield during close cleanup is a compute error YIELD_IN_CLOSE.
func TestYieldInCloseIsError(t *testing.T) {
	src := `
gen fn bad() {
  try {
    yield 1
  } finally {
    yield 2
  }
}
fn main() { return 0 }
`
	acts := []difftest.Action{
		{Kind: difftest.ActNew, Target: 0, GenName: "bad"},
		{Kind: difftest.ActNext, Target: 0},
		{Kind: difftest.ActClose, Target: 0},
		{Kind: difftest.ActStatus, Target: 0},
	}
	rep := runScript(t, "yield_in_close", src, acts)
	ev := rep.Events
	assertKindCode(t, ev[2], "COMPUTE", "YIELD_IN_CLOSE")
	if got := ev[3].FSM.Status; got != "closed" {
		t.Fatalf("status after yield-in-close = %s, want closed", got)
	}
}

func assertState(t *testing.T, ev difftest.Event, state, val string) {
	t.Helper()
	if ev.FSM.State != state || ev.Ref.State != state {
		t.Fatalf("run %d: state fsm=%q ref=%q want %q", ev.Run, ev.FSM.State, ev.Ref.State, state)
	}
	if val != "" && (ev.FSM.Val != val || ev.Ref.Val != val) {
		t.Fatalf("run %d: val fsm=%q ref=%q want %q", ev.Run, ev.FSM.Val, ev.Ref.Val, val)
	}
}

func assertCode(t *testing.T, ev difftest.Event, state, code string) {
	t.Helper()
	if ev.FSM.State != state || ev.Ref.State != state {
		t.Fatalf("run %d: state fsm=%q ref=%q want %q", ev.Run, ev.FSM.State, ev.Ref.State, state)
	}
	if ev.FSM.ErrCode != code || ev.Ref.ErrCode != code {
		t.Fatalf("run %d: code fsm=%q ref=%q want %q", ev.Run, ev.FSM.ErrCode, ev.Ref.ErrCode, code)
	}
}

func assertKindCode(t *testing.T, ev difftest.Event, kind, code string) {
	t.Helper()
	if ev.FSM.ErrKind != kind || ev.Ref.ErrKind != kind {
		t.Fatalf("run %d: kind fsm=%q ref=%q want %q", ev.Run, ev.FSM.ErrKind, ev.Ref.ErrKind, kind)
	}
	if ev.FSM.ErrCode != code || ev.Ref.ErrCode != code {
		t.Fatalf("run %d: code fsm=%q ref=%q want %q", ev.Run, ev.FSM.ErrCode, ev.Ref.ErrCode, code)
	}
}

func assertEx(t *testing.T, ev difftest.Event, name, msg string) {
	t.Helper()
	if ev.FSM.State != "error" || ev.Ref.State != "error" {
		t.Fatalf("run %d: state fsm=%q ref=%q want error", ev.Run, ev.FSM.State, ev.Ref.State)
	}
	if ev.FSM.ExName != name || ev.Ref.ExName != name {
		t.Fatalf("run %d: exname fsm=%q ref=%q want %q", ev.Run, ev.FSM.ExName, ev.Ref.ExName, name)
	}
	if ev.FSM.ExMessage != msg || ev.Ref.ExMessage != msg {
		t.Fatalf("run %d: exmsg fsm=%q ref=%q want %q", ev.Run, ev.FSM.ExMessage, ev.Ref.ExMessage, msg)
	}
}

var _ = strv
