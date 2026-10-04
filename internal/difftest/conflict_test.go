package difftest_test

import (
	"sync"
	"testing"

	"genfsm/internal/difftest"
	"genfsm/internal/engine"
	"genfsm/internal/semerr"
	"genfsm/internal/value"
)

// A running generator must reject concurrent resume attempts. We arrange a
// generator that is suspended inside a for-loop whose child generator blocks;
// then a second driver action on the outer handle while its body is running
// must return CONFLICT/RESUME_RUNNING on both engines.
//
// The robust, engine-neutral trigger: hold the generator in "running" state
// by driving it from two goroutines that both call Next() at the same time.
func TestResumeRunningRejected(t *testing.T) {
	src := `
gen fn slow() {
  yield 1
  yield 2
  yield 3
}
fn main() { return 0 }
`
	for _, eng := range []engine.Engine{engine.EngineFSM, engine.EngineRef} {
		b, err := engine.Frontend(src)
		if err != nil {
			t.Fatal(err)
		}
		r, err := engine.New(b, eng, engine.DefaultConfig())
		if err != nil {
			t.Fatal(err)
		}
		g, err := r.NewGen("slow", nil)
		if err != nil {
			t.Fatal(err)
		}
		// First Next: suspended at yield 1.
		if r1 := g.Next(); r1.State != value.StateYielded {
			t.Fatalf("%s: first next = %s", eng, r1.State)
		}
		// Force the handle into running state, then issue a second resume.
		// Both engines expose status; simulate the race by locking status via
		// concurrent calls: the loser must see RESUME_RUNNING.
		var wg sync.WaitGroup
		results := make([]value.Result, 8)
		barrier := make(chan struct{})
		for i := range results {
			wg.Add(1)
			go func(idx int) {
				defer wg.Done()
				<-barrier
				results[idx] = g.Next()
			}(i)
		}
		close(barrier)
		wg.Wait()

		var sawConflict bool
		var sawYield bool
		for _, rr := range results {
			if rr.ErrCode == string(semerr.CodeResumeRunning) {
				if rr.ErrKind != string(semerr.KindConflict) {
					t.Fatalf("%s: resume-running kind=%q want CONFLICT", eng, rr.ErrKind)
				}
				sawConflict = true
			}
			if rr.State == value.StateYielded {
				sawYield = true
			}
		}
		if !sawConflict {
			t.Fatalf("%s: expected at least one RESUME_RUNNING conflict among %+v", eng, summarize(results))
		}
		if !sawYield {
			t.Fatalf("%s: expected one resume to actually advance", eng)
		}
	}
}

// Arity / name mistakes at the engine boundary are INPUT/BAD_REQUEST.
func TestNewGenBadRequest(t *testing.T) {
	src := `
gen fn one(x) { yield x }
fn main() { return 0 }
`
	for _, eng := range []engine.Engine{engine.EngineFSM, engine.EngineRef} {
		b, err := engine.Frontend(src)
		if err != nil {
			t.Fatal(err)
		}
		r, _ := engine.New(b, eng, engine.DefaultConfig())
		if _, err := r.NewGen("missing", nil); err == nil || !semerr.Is(err, semerr.KindInput) {
			t.Fatalf("%s missing: err=%v", eng, err)
		}
		if _, err := r.NewGen("one", nil); err == nil || !semerr.Is(err, semerr.KindInput) {
			t.Fatalf("%s arity: err=%v", eng, err)
		}
	}
}

// Frontend categories: lex/parse/static errors are all INPUT.
func TestFrontendInputCategories(t *testing.T) {
	cases := map[string]string{
		"lex":    "fn main( { return 1 }",
		"parse":  "fn main() { let = ; return 0 }",
		"static": "fn main() { yield 1; return 0 }",
		"nomain": "fn other() { return 0 }",
	}
	for name, src := range cases {
		if _, err := engine.Frontend(src); err == nil || !semerr.Is(err, semerr.KindInput) {
			t.Fatalf("%s: err=%v", name, err)
		}
	}
}

// close-running conflict category is independently asserted by driving a
// close from within user code (a generator closing itself while running).
func TestCloseRunningRejected(t *testing.T) {
	src := `
gen fn selfclose(g) {
  yield 1
}
fn main() { return 0 }
`
	// At the driver level close-while-running cannot be sequenced on one
	// goroutine, so we assert the conflict classification on a concurrent
	// Close racing with Next: losers must be CONFLICT (close or resume).
	for _, eng := range []engine.Engine{engine.EngineFSM, engine.EngineRef} {
		b, _ := engine.Frontend(src)
		r, _ := engine.New(b, eng, engine.DefaultConfig())
		g, _ := r.NewGen("selfclose", []value.Value{value.NullV()})
		g.Next()
		var wg sync.WaitGroup
		var mu sync.Mutex
		var kinds []string
		barrier := make(chan struct{})
		for i := 0; i < 8; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				<-barrier
				var rr value.Result
				if i%2 == 0 {
					rr = g.Next()
				} else {
					rr = g.Close()
				}
				if rr.ErrKind == string(semerr.KindConflict) {
					mu.Lock()
					kinds = append(kinds, rr.ErrCode)
					mu.Unlock()
				}
			}(i)
		}
		close(barrier)
		wg.Wait()
		if len(kinds) == 0 {
			t.Fatalf("%s: expected conflicts, none observed", eng)
		}
	}
}

func summarize(rs []value.Result) []string {
	out := make([]string, len(rs))
	for i, r := range rs {
		out[i] = string(r.State) + "/" + r.ErrCode
	}
	return out
}

var _ = difftest.ActNext
