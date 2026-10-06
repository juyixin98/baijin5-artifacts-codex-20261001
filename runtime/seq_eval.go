package runtime

import (
	"fmt"

	"pmd/frontend"
)

// EvalSequential is the reference interpreter: it tries branches in their
// original order, matching patterns structurally and evaluating guards on
// demand. It shares only the guard-expression evaluator with the decision
// tree engine; the matching strategy is implemented independently.
func EvalSequential(prog *frontend.Program, v *Value) Outcome {
	o := Outcome{Branch: -1, Steps: []string{}}
	if f := v.Validate(prog.Ctors); f != nil {
		o.Failure = f
		o.Steps = append(o.Steps, "value validation failed: "+f.Detail)
		return o
	}
	for i := range prog.Branches {
		b := &prog.Branches[i]
		binds := map[string]*Value{}
		if !matchPattern(b.Pat, v, binds) {
			o.Steps = append(o.Steps, fmt.Sprintf("branch %d: structural mismatch", i))
			continue
		}
		if b.Guard != nil {
			lk := func(name string) (*Value, bool) {
				val, ok := binds[name]
				return val, ok
			}
			res, err := EvalGuard(b.Guard, lk, &o.Effects)
			if err != nil {
				o.Failure = &Failure{Category: CatGuardError, Detail: fmt.Sprintf("branch %d: %s", i, err)}
				o.Steps = append(o.Steps, fmt.Sprintf("branch %d: guard error: %s", i, err))
				return o
			}
			if res.Kind != frontend.LitBool {
				o.Failure = &Failure{Category: CatGuardError, Detail: fmt.Sprintf("branch %d: guard evaluated to %s, not a boolean", i, res)}
				return o
			}
			o.Steps = append(o.Steps, fmt.Sprintf("branch %d: guard => %v", i, res.Bool))
			if !res.Bool {
				continue
			}
		}
		o.Matched = true
		o.Branch = i
		o.Label = b.Label
		if len(binds) > 0 {
			o.Bindings = binds
		}
		o.Steps = append(o.Steps, fmt.Sprintf("branch %d: matched label %q", i, b.Label))
		return o
	}
	o.Failure = &Failure{Category: CatNoMatch, Detail: "no branch matched"}
	o.Steps = append(o.Steps, "no branch matched")
	return o
}

// matchPattern matches structurally, extending binds on success. A failed
// attempt may leave partial bindings in binds; callers use a fresh map per
// branch, so failed bindings never leak into later branches.
func matchPattern(p frontend.Pattern, v *Value, binds map[string]*Value) bool {
	switch t := p.(type) {
	case frontend.PWildcard:
		return true
	case frontend.PVar:
		binds[t.Name] = v
		return true
	case frontend.PLit:
		return v.Kind == VLit && v.Lit == t.Val
	case frontend.PCtor:
		if v.Kind != VCtor || v.Ctor != t.Name || len(v.Args) != len(t.Args) {
			return false
		}
		for i, sub := range t.Args {
			if !matchPattern(sub, v.Args[i], binds) {
				return false
			}
		}
		return true
	}
	return false
}
