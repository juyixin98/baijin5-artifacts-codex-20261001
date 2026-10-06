// Package tests contains black-box tests that exercise the public APIs of
// the frontend, ir, runtime, diff and service modules. Expected results
// are asserted concretely; they are not derived from the implementation.
package tests

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"pmd/frontend"
	"pmd/ir"
	"pmd/runtime"
)

func parse(t *testing.T, src string) *frontend.Program {
	t.Helper()
	prog, err := frontend.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return prog
}

func loadProgram(t *testing.T, rel string) *frontend.Program {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "fixtures", rel))
	if err != nil {
		t.Fatalf("read fixture %s: %v", rel, err)
	}
	prog, err := frontend.Parse(string(b))
	if err != nil {
		t.Fatalf("parse fixture %s: %v", rel, err)
	}
	return prog
}

func lit(n int64) *runtime.Value { return runtime.Lit(frontend.IntLit(n)) }

func ctor(name string, args ...*runtime.Value) *runtime.Value {
	return runtime.Ctor(name, args...)
}

func cons(h, tl *runtime.Value) *runtime.Value { return runtime.Ctor("Cons", h, tl) }

func wantMatch(t *testing.T, o runtime.Outcome, branch int, label string) {
	t.Helper()
	if o.Failure != nil {
		t.Fatalf("unexpected failure: %+v (steps: %v)", o.Failure, o.Steps)
	}
	if !o.Matched || o.Branch != branch || o.Label != label {
		t.Fatalf("outcome = matched:%v branch:%d label:%q, want branch %d %q (steps: %v)",
			o.Matched, o.Branch, o.Label, branch, label, o.Steps)
	}
}

func wantFailure(t *testing.T, o runtime.Outcome, category string) {
	t.Helper()
	if o.Matched {
		t.Fatalf("outcome matched branch %d, want failure %q", o.Branch, category)
	}
	if o.Failure == nil || o.Failure.Category != category {
		t.Fatalf("failure = %+v, want category %q", o.Failure, category)
	}
}

func wantEffects(t *testing.T, o runtime.Outcome, want ...string) {
	t.Helper()
	got := make([]string, len(o.Effects))
	for i, e := range o.Effects {
		got[i] = e.Label + "=" + e.Value.String()
	}
	if len(got) != len(want) {
		t.Fatalf("effects = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("effects = %v, want %v", got, want)
		}
	}
}

// Branch order priority: an earlier, more general branch wins over a
// later, more specific one, and the shadowed branch is reported.
func TestBranchOrderPriority(t *testing.T) {
	prog := parse(t, `
ctor A 0
ctor B 0
ctor P 2

match v {
  | P(_, _) => "any-pair"
  | P(A, B) => "specific"
  | A => "just-a"
  | _ => "fallback"
}
`)
	tr := ir.Compile(prog)
	if len(tr.Warnings) != 1 || !strings.Contains(tr.Warnings[0], "branch 1") {
		t.Errorf("warnings = %v, want one about unreachable branch 1", tr.Warnings)
	}
	o := runtime.EvalTree(tr, ctor("P", ctor("A"), ctor("B")))
	wantMatch(t, o, 0, "any-pair")
	o = runtime.EvalTree(tr, ctor("A"))
	wantMatch(t, o, 2, "just-a")
	o = runtime.EvalTree(tr, ctor("B"))
	wantMatch(t, o, 3, "fallback")
}

// Guards run at most once, in branch order, and only when their own
// branch's structural pattern matched.
func TestGuardEvaluatedOnceInOrderNotEarly(t *testing.T) {
	prog := loadProgram(t, "programs/guards.pmd")
	tr := ir.Compile(prog)

	o := runtime.EvalTree(tr, ctor("C", lit(15), lit(3)))
	wantMatch(t, o, 0, "big-first")
	wantEffects(t, o, "g1=true") // g2 never evaluated

	o = runtime.EvalTree(tr, ctor("C", lit(5), lit(3)))
	wantMatch(t, o, 2, "plain")
	wantEffects(t, o, "g1=false", "g2=false") // each exactly once, in order

	o = runtime.EvalTree(tr, ctor("C", lit(5), lit(-2)))
	wantMatch(t, o, 1, "neg-second")
	wantEffects(t, o, "g1=false", "g2=true")

	// Structural mismatch on C(...) branches: no guard runs at all.
	o = runtime.EvalTree(tr, ctor("D"))
	wantMatch(t, o, 3, "unit")
	wantEffects(t, o)
}

// Nested constructor patterns bind variables at deep paths.
func TestNestedFieldAccess(t *testing.T) {
	prog := loadProgram(t, "programs/tree.pmd")
	tr := ir.Compile(prog)
	leaf := func() *runtime.Value { return ctor("Leaf") }
	node := func(l, r *runtime.Value) *runtime.Value { return ctor("Node", l, r) }

	o := runtime.EvalTree(tr, node(node(leaf(), leaf()), leaf()))
	wantMatch(t, o, 0, "deep-left")
	if b := o.Bindings["x"]; b == nil || b.Kind != runtime.VCtor || b.Ctor != "Leaf" {
		t.Errorf("binding x = %v, want Leaf", o.Bindings["x"])
	}

	o = runtime.EvalTree(tr, node(leaf(), node(leaf(), leaf())))
	wantMatch(t, o, 1, "deep-right")
	if b := o.Bindings["r"]; b == nil || b.Kind != runtime.VCtor || b.Ctor != "Leaf" {
		t.Errorf("binding r = %v, want Leaf", o.Bindings["r"])
	}

	o = runtime.EvalTree(tr, node(leaf(), node(leaf(), node(leaf(), leaf()))))
	wantMatch(t, o, 2, "any-node")

	o = runtime.EvalTree(tr, leaf())
	wantMatch(t, o, 3, "leaf")
}

// Each failure category is produced with the right classification.
func TestFailureCategories(t *testing.T) {
	prog := loadProgram(t, "programs/list.pmd")
	tr := ir.Compile(prog)

	wantFailure(t, runtime.EvalTree(tr, cons(lit(1), lit(5))), runtime.CatNoMatch)
	wantFailure(t, runtime.EvalTree(tr, lit(42)), runtime.CatNoMatch)
	wantFailure(t, runtime.EvalTree(tr, ctor("Cons", lit(1))), runtime.CatInvalidValue)
	wantFailure(t, runtime.EvalTree(tr, ctor("Wat")), runtime.CatInvalidValue)

	guardProg := parse(t, `
ctor Some 1
ctor None 0

match v {
  | Some(0) => "zero"
  | Some(x) if gt(x, 100) => "big"
  | Some(_) => "other"
  | None => "none"
}
`)
	gtr := ir.Compile(guardProg)
	wantFailure(t, runtime.EvalTree(gtr,
		ctor("Some", runtime.Lit(frontend.StrLit("hi")))), runtime.CatGuardError)

	// Frontend errors are categorized too.
	if _, err := frontend.Parse("match v { | A(x) = \"b\" }"); err == nil {
		t.Error("expected parse error")
	} else if fe, ok := err.(*frontend.Error); !ok || fe.Category != frontend.CatParse {
		t.Errorf("err = %v, want category parse_error", err)
	}
	if _, err := frontend.Parse("ctor A 1\nmatch v {\n | A(x) if gt(z, 1) => \"b\"\n}"); err == nil {
		t.Error("expected semantic error")
	} else if fe, ok := err.(*frontend.Error); !ok || fe.Category != frontend.CatSemantic {
		t.Errorf("err = %v, want category semantic_error", err)
	}
}

// Bindings of a branch whose guard failed must not leak into the branch
// that eventually matches; each branch sees only its own variables.
func TestBindingScopeAcrossBranches(t *testing.T) {
	prog := parse(t, `
ctor P 2
ctor Q 1

match v {
  | P(x, y) if effect("g", gt(x, y)) => "gt"
  | P(a, b) => "any-p"
  | Q(z) => "q"
}
`)
	tr := ir.Compile(prog)
	o := runtime.EvalTree(tr, ctor("P", lit(1), lit(2)))
	wantMatch(t, o, 1, "any-p")
	wantEffects(t, o, "g=false")
	if _, ok := o.Bindings["x"]; ok {
		t.Errorf("binding x leaked from failed guarded branch: %+v", o.Bindings)
	}
	if _, ok := o.Bindings["y"]; ok {
		t.Errorf("binding y leaked from failed guarded branch: %+v", o.Bindings)
	}
	if o.Bindings["a"].Lit.Int != 1 || o.Bindings["b"].Lit.Int != 2 {
		t.Errorf("bindings = %+v, want a=1 b=2", o.Bindings)
	}
}

// Literal patterns, including boundary comparisons against the literal pool.
func TestLiteralPatterns(t *testing.T) {
	prog := parse(t, `
ctor Some 1
ctor None 0

match v {
  | Some(0) => "zero"
  | Some(x) if gt(x, 100) => "big"
  | Some(_) => "other"
  | None => "none"
}
`)
	tr := ir.Compile(prog)
	wantMatch(t, runtime.EvalTree(tr, ctor("Some", lit(0))), 0, "zero")
	wantMatch(t, runtime.EvalTree(tr, ctor("Some", lit(101))), 1, "big")
	wantMatch(t, runtime.EvalTree(tr, ctor("Some", lit(100))), 2, "other")
	wantMatch(t, runtime.EvalTree(tr, ctor("Some", lit(-1))), 2, "other")
	wantMatch(t, runtime.EvalTree(tr, ctor("None")), 3, "none")
	// A string payload skips the literal-0 arm and lands in the guard arm,
	// where gt("a", 100) is a type error.
	wantFailure(t, runtime.EvalTree(tr, ctor("Some", runtime.Lit(frontend.StrLit("a")))),
		runtime.CatGuardError)
}

// The compiled tree shares tests: the root constructor test is performed
// once for all four Cons branches.
func TestSharedTestStructure(t *testing.T) {
	prog := loadProgram(t, "programs/list.pmd")
	tr := ir.Compile(prog)
	if tr.Stats.Switches != 3 {
		t.Errorf("switches = %d, want 3", tr.Stats.Switches)
	}
	if tr.Root.Kind != ir.Switch || len(tr.Root.Cases) != 2 {
		t.Fatalf("root = %+v", tr.Root)
	}
	// Guard nodes sit below the structural tests, never above them.
	consArm := tr.Root.Cases[0].Node
	if consArm.Kind != ir.Switch {
		t.Errorf("Cons arm kind = %s, want switch (guards must come after structure)", consArm.Kind)
	}
}
