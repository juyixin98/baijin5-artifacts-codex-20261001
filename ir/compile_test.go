package ir

import (
	"strings"
	"testing"

	"pmd/frontend"
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

func compileSrc(t *testing.T, src string) *Tree {
	t.Helper()
	prog, err := frontend.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return Compile(prog)
}

func TestCompileListShape(t *testing.T) {
	tr := compileSrc(t, listSrc)
	if tr.Stats.Switches != 3 {
		t.Errorf("switches = %d, want 3 (root, $.1, $.1.1)", tr.Stats.Switches)
	}
	if tr.Stats.Guards != 2 {
		t.Errorf("guards = %d, want 2", tr.Stats.Guards)
	}
	if len(tr.Warnings) != 0 {
		t.Errorf("unexpected warnings: %v", tr.Warnings)
	}
	if tr.Root.Kind != Switch {
		t.Fatalf("root kind = %s", tr.Root.Kind)
	}
	if tr.Root.Path.String() != "$" {
		t.Errorf("root path = %s", tr.Root.Path)
	}
	if len(tr.Root.Cases) != 2 ||
		tr.Root.Cases[0].Test.Ctor != "Cons" ||
		tr.Root.Cases[1].Test.Ctor != "Nil" {
		t.Errorf("root cases = %+v", tr.Root.Cases)
	}
	// The Cons arm must switch on the tail path $.1 next (test sharing).
	consArm := tr.Root.Cases[0].Node
	if consArm.Kind != Switch || consArm.Path.String() != "$.1" {
		t.Errorf("Cons arm = kind %s path %s, want switch on $.1", consArm.Kind, consArm.Path)
	}
	// The Nil arm is the "empty" leaf directly.
	nilArm := tr.Root.Cases[1].Node
	if nilArm.Kind != Leaf || nilArm.Branch != 4 {
		t.Errorf("Nil arm = kind %s branch %d, want leaf branch 4", nilArm.Kind, nilArm.Branch)
	}
}

func TestUnreachableBranchWarning(t *testing.T) {
	tr := compileSrc(t, "ctor A 0\nctor B 0\n\nmatch v {\n  | _ => \"wild\"\n  | A => \"a\"\n}\n")
	if len(tr.Warnings) != 1 || !strings.Contains(tr.Warnings[0], "branch 1") {
		t.Errorf("warnings = %v, want one about branch 1", tr.Warnings)
	}
}

// TestNoRepeatedPathOnAnyRoute is the "shared test" property: walking from
// the root to any leaf never tests the same value path twice.
func TestNoRepeatedPathOnAnyRoute(t *testing.T) {
	srcs := []string{
		listSrc,
		`ctor Leaf 0
ctor Node 2
match t {
  | Node(Node(Leaf, x), Leaf) => "deep-left"
  | Node(_, Node(r, Leaf)) => "deep-right"
  | Node(_, _) => "any-node"
  | Leaf => "leaf"
}
`,
		`ctor Some 1
ctor None 0
match v {
  | Some(0) => "zero"
  | Some(x) if gt(x, 100) => "big"
  | Some(_) => "other"
  | None => "none"
}
`,
	}
	for _, src := range srcs {
		tr := compileSrc(t, src)
		var check func(n *Node, seen map[string]bool)
		check = func(n *Node, seen map[string]bool) {
			switch n.Kind {
			case Switch:
				p := n.Path.String()
				if seen[p] {
					t.Errorf("path %s tested twice on one route", p)
				}
				seen[p] = true
				for _, c := range n.Cases {
					check(c.Node, seen)
				}
				check(n.Default, seen)
				delete(seen, p)
			case GuardN:
				check(n.Then, seen)
				check(n.Else, seen)
			case Leaf, Fail:
			default:
				t.Errorf("unknown node kind %q", n.Kind)
			}
		}
		check(tr.Root, map[string]bool{})
	}
}

func TestGuardBindingsResolved(t *testing.T) {
	tr := compileSrc(t, listSrc)
	// Find the guard nodes and check their bindings point at real paths.
	var guards []*Node
	var walk func(n *Node)
	walk = func(n *Node) {
		switch n.Kind {
		case Switch:
			for _, c := range n.Cases {
				walk(c.Node)
			}
			walk(n.Default)
		case GuardN:
			guards = append(guards, n)
			walk(n.Then)
			walk(n.Else)
		}
	}
	walk(tr.Root)
	if len(guards) != 2 {
		t.Fatalf("found %d guard nodes, want 2", len(guards))
	}
	// pair-check guard binds x@$.0 and y@$.1.0
	g0 := guards[0]
	if g0.ExprText != `effect("pair-check", eq(x, y))` {
		t.Errorf("guard 0 expr = %q", g0.ExprText)
	}
	want := map[string]string{"x": "$.0", "y": "$.1.0"}
	for _, b := range g0.Bindings {
		if w, ok := want[b.Name]; ok {
			if b.Path.String() != w {
				t.Errorf("binding %s at %s, want %s", b.Name, b.Path, w)
			}
			delete(want, b.Name)
		}
	}
	if len(want) != 0 {
		t.Errorf("missing bindings: %v (have %+v)", want, g0.Bindings)
	}
}
