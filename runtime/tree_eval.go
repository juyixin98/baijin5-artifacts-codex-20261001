package runtime

import (
	"fmt"

	"pmd/frontend"
	"pmd/ir"
)

// EvalTree runs a compiled decision tree against a value.
func EvalTree(t *ir.Tree, v *Value) Outcome {
	o := Outcome{Branch: -1, Steps: []string{}}
	if f := v.Validate(t.Ctors); f != nil {
		o.Failure = f
		o.Steps = append(o.Steps, "value validation failed: "+f.Detail)
		return o
	}
	ev := &treeEval{root: v, o: &o}
	ev.walk(t.Root)
	return o
}

type treeEval struct {
	root *Value
	o    *Outcome
	done bool
}

func (e *treeEval) walk(n *ir.Node) {
	if e.done {
		return
	}
	switch n.Kind {
	case ir.Fail:
		e.o.Failure = &Failure{Category: CatNoMatch, Detail: "no branch matched"}
		e.o.Steps = append(e.o.Steps, fmt.Sprintf("fail#%d: no matching branch", n.ID))
		e.done = true
	case ir.Leaf:
		e.o.Matched = true
		e.o.Branch = n.Branch
		e.o.Label = n.Label
		e.o.Bindings = resolveBindings(n.Bindings, e.root)
		e.o.Steps = append(e.o.Steps, fmt.Sprintf("leaf#%d: branch %d label %q", n.ID, n.Branch, n.Label))
		e.done = true
	case ir.Switch:
		v, err := valueAt(e.root, n.Path)
		if err != nil {
			e.o.Failure = &Failure{Category: CatInternal, Detail: err.Error(), Path: n.Path.String()}
			e.done = true
			return
		}
		key := keyOf(v)
		for _, c := range n.Cases {
			if keyMatches(c.Test, key) {
				e.o.Steps = append(e.o.Steps, fmt.Sprintf("switch#%d %s: %s => case %s -> node #%d", n.ID, n.Path, key, c.Test, c.Node.ID))
				e.walk(c.Node)
				return
			}
		}
		e.o.Steps = append(e.o.Steps, fmt.Sprintf("switch#%d %s: %s => default -> node #%d", n.ID, n.Path, key, n.Default.ID))
		e.walk(n.Default)
	case ir.GuardN:
		lk := lookupFromBindings(n.Bindings, e.root)
		res, err := EvalGuard(n.Expr, lk, &e.o.Effects)
		if err != nil {
			e.o.Failure = &Failure{Category: CatGuardError, Detail: err.Error()}
			e.o.Steps = append(e.o.Steps, fmt.Sprintf("guard#%d (%s) => error: %s", n.ID, n.ExprText, err))
			e.done = true
			return
		}
		if res.Kind != frontend.LitBool {
			e.o.Failure = &Failure{Category: CatGuardError, Detail: fmt.Sprintf("guard evaluated to %s, not a boolean", res)}
			e.done = true
			return
		}
		e.o.Steps = append(e.o.Steps, fmt.Sprintf("guard#%d (%s) => %v", n.ID, n.ExprText, res.Bool))
		if res.Bool {
			e.walk(n.Then)
		} else {
			e.walk(n.Else)
		}
	}
}

func valueAt(root *Value, path ir.Path) (*Value, error) {
	v := root
	for _, i := range path {
		if v.Kind != VCtor || i >= len(v.Args) {
			return nil, fmt.Errorf("path %s not present in value %s", path, root)
		}
		v = v.Args[i]
	}
	return v, nil
}

func keyOf(v *Value) ir.TestKey {
	if v.Kind == VCtor {
		return ir.TestKey{Ctor: v.Ctor}
	}
	l := v.Lit
	return ir.TestKey{Lit: &l}
}

func keyMatches(test ir.TestKey, key ir.TestKey) bool {
	if test.Ctor != "" || key.Ctor != "" {
		return test.Ctor != "" && test.Ctor == key.Ctor
	}
	if test.Lit == nil || key.Lit == nil {
		return false
	}
	return *test.Lit == *key.Lit
}

func resolveBindings(binds []ir.Binding, root *Value) map[string]*Value {
	if len(binds) == 0 {
		return nil
	}
	out := make(map[string]*Value, len(binds))
	for _, b := range binds {
		v, err := valueAt(root, b.Path)
		if err != nil {
			continue // cannot happen for a validated value
		}
		out[b.Name] = v
	}
	return out
}

func lookupFromBindings(binds []ir.Binding, root *Value) Lookup {
	m := resolveBindings(binds, root)
	return func(name string) (*Value, bool) {
		v, ok := m[name]
		return v, ok
	}
}
