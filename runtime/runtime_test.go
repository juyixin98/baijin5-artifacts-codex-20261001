package runtime

import (
	"testing"

	"pmd/frontend"
	"pmd/ir"
)

const listSrc = `
ctor Nil 0
ctor Cons 2

match xs {
  | Cons(x, Cons(y, Nil)) if effect("pair-check", eq(x, y)) => "pair-eq"
  | Cons(x, Cons(_, _)) => "long"
  | Cons(x, Nil) if even(x) => "single-even"
  | Cons(_, Nil) => "single"
  | Nil => "empty"
}
`

func setup(t *testing.T, src string) (*frontend.Program, *ir.Tree) {
	t.Helper()
	prog, err := frontend.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return prog, ir.Compile(prog)
}

func vint(n int64) *Value { return Lit(frontend.IntLit(n)) }

func cons(h, tl *Value) *Value { return Ctor("Cons", h, tl) }

func nilv() *Value { return Ctor("Nil") }

func wantMatch(t *testing.T, o Outcome, branch int, label string) {
	t.Helper()
	if o.Failure != nil {
		t.Fatalf("unexpected failure: %+v", o.Failure)
	}
	if !o.Matched || o.Branch != branch || o.Label != label {
		t.Fatalf("outcome = matched:%v branch:%d label:%q, want branch %d %q",
			o.Matched, o.Branch, o.Label, branch, label)
	}
}

func wantEffects(t *testing.T, o Outcome, want ...Effect) {
	t.Helper()
	if len(o.Effects) != len(want) {
		t.Fatalf("effects = %+v, want %+v", o.Effects, want)
	}
	for i, e := range want {
		if o.Effects[i] != e {
			t.Errorf("effect %d = %+v, want %+v", i, o.Effects[i], e)
		}
	}
}

func eff(label string, v bool) Effect {
	return Effect{Label: label, Value: frontend.BoolLit(v)}
}

func TestTreePairEq(t *testing.T) {
	_, tr := setup(t, listSrc)
	o := EvalTree(tr, cons(vint(2), cons(vint(2), nilv())))
	wantMatch(t, o, 0, "pair-eq")
	wantEffects(t, o, eff("pair-check", true))
	if o.Bindings["x"].Lit.Int != 2 || o.Bindings["y"].Lit.Int != 2 {
		t.Errorf("bindings = %+v", o.Bindings)
	}
	if len(o.Steps) == 0 {
		t.Error("expected a non-empty step trace")
	}
}

func TestTreeGuardFalseFallsThrough(t *testing.T) {
	_, tr := setup(t, listSrc)
	// pair-check fails once (eq(2,3)=false), then branch 1 matches.
	o := EvalTree(tr, cons(vint(2), cons(vint(3), nilv())))
	wantMatch(t, o, 1, "long")
	wantEffects(t, o, eff("pair-check", false))
	if len(o.Bindings) != 1 || o.Bindings["x"].Lit.Int != 2 {
		t.Errorf("bindings = %+v, want only x=2", o.Bindings)
	}
}

func TestTreeSingleEvenAndOdd(t *testing.T) {
	_, tr := setup(t, listSrc)
	o := EvalTree(tr, cons(vint(4), nilv()))
	wantMatch(t, o, 2, "single-even")
	wantEffects(t, o) // even() is pure: no effects
	o = EvalTree(tr, cons(vint(3), nilv()))
	wantMatch(t, o, 3, "single")
	wantEffects(t, o)
}

func TestTreeEmptyAndNoMatch(t *testing.T) {
	_, tr := setup(t, listSrc)
	o := EvalTree(tr, nilv())
	wantMatch(t, o, 4, "empty")
	// pair-check guard must not have been evaluated for Nil.
	wantEffects(t, o)

	o = EvalTree(tr, cons(vint(1), vint(5)))
	if o.Matched || o.Failure == nil || o.Failure.Category != CatNoMatch {
		t.Errorf("outcome = %+v, want no_match", o)
	}
}

func TestTreeInvalidValue(t *testing.T) {
	_, tr := setup(t, listSrc)
	o := EvalTree(tr, Ctor("Cons", vint(1)))
	if o.Failure == nil || o.Failure.Category != CatInvalidValue {
		t.Errorf("outcome = %+v, want invalid_value", o)
	}
	o = EvalTree(tr, Ctor("Wat"))
	if o.Failure == nil || o.Failure.Category != CatInvalidValue {
		t.Errorf("outcome = %+v, want invalid_value", o)
	}
}

func TestTreeGuardError(t *testing.T) {
	src := `
ctor Some 1
ctor None 0

match v {
  | Some(0) => "zero"
  | Some(x) if gt(x, 100) => "big"
  | Some(_) => "other"
  | None => "none"
}
`
	_, tr := setup(t, src)
	o := EvalTree(tr, Ctor("Some", Lit(frontend.StrLit("hi"))))
	if o.Failure == nil || o.Failure.Category != CatGuardError {
		t.Errorf("outcome = %+v, want guard_error", o)
	}
}

// TestSequentialAgreement spot-checks that both engines agree on a small
// table of values; the exhaustive differential campaign lives in the
// diff module and the black-box tests.
func TestSequentialAgreement(t *testing.T) {
	prog, tr := setup(t, listSrc)
	values := []*Value{
		nilv(),
		cons(vint(2), nilv()),
		cons(vint(3), nilv()),
		cons(vint(2), cons(vint(2), nilv())),
		cons(vint(2), cons(vint(3), nilv())),
		cons(vint(1), cons(vint(2), cons(vint(3), nilv()))),
		cons(vint(1), vint(5)),
		vint(7),
	}
	for _, v := range values {
		a := EvalTree(tr, v)
		b := EvalSequential(prog, v)
		if a.Matched != b.Matched || a.Branch != b.Branch || a.Label != b.Label {
			t.Errorf("value %s: tree=%+v seq=%+v", v, a, b)
			continue
		}
		if (a.Failure == nil) != (b.Failure == nil) ||
			a.Failure != nil && a.Failure.Category != b.Failure.Category {
			t.Errorf("value %s: failure mismatch tree=%+v seq=%+v", v, a.Failure, b.Failure)
		}
		if len(a.Effects) != len(b.Effects) {
			t.Errorf("value %s: effects tree=%+v seq=%+v", v, a.Effects, b.Effects)
		}
	}
}
