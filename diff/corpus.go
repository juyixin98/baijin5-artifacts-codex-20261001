// Package diff provides semantic differential testing: it runs the
// compiled decision tree and the sequential reference interpreter over
// generated corpora and golden fixtures, and reports where they disagree.
package diff

import (
	"encoding/json"
	"math/rand"
	"pmd/frontend"
	"pmd/runtime"
)

// Version is the module version reported by the service layer.
const Version = "0.1.0"

// LitPool is the fixed literal pool used for corpus generation. It mixes
// small boundary integers with a string and a boolean so that literal
// patterns both hit and miss, and so that guard type errors are exercised.
func LitPool() []frontend.LitValue {
	return []frontend.LitValue{
		frontend.IntLit(-1),
		frontend.IntLit(0),
		frontend.IntLit(1),
		frontend.IntLit(2),
		frontend.IntLit(3),
		frontend.IntLit(10),
		frontend.IntLit(101),
		frontend.StrLit("a"),
		frontend.BoolLit(true),
	}
}

// Enumerate deterministically lists values by increasing height
// (literals and nullary constructors have height 0) until the limit is
// reached. Duplicate values are removed.
func Enumerate(ctors map[string]int, order []string, maxDepth, limit int) []*runtime.Value {
	pool := LitPool()
	seen := map[string]bool{}
	var all []*runtime.Value
	add := func(v *runtime.Value) bool {
		key := canonical(v)
		if seen[key] {
			return true
		}
		seen[key] = true
		all = append(all, v)
		return len(all) < limit
	}
	for _, l := range pool {
		if !add(runtime.Lit(l)) {
			return all
		}
	}
	for _, name := range order {
		if ctors[name] == 0 {
			if !add(runtime.Ctor(name)) {
				return all
			}
		}
	}
	prev := append([]*runtime.Value{}, all...)
	for d := 1; d <= maxDepth; d++ {
		var cur []*runtime.Value
		full := false
		for _, name := range order {
			arity := ctors[name]
			if arity == 0 {
				continue
			}
			var gen func(prefix []*runtime.Value)
			gen = func(prefix []*runtime.Value) {
				if full {
					return
				}
				if len(prefix) == arity {
					args := append([]*runtime.Value{}, prefix...)
					v := runtime.Ctor(name, args...)
					key := canonical(v)
					if !seen[key] {
						seen[key] = true
						cur = append(cur, v)
						if len(all)+len(cur) >= limit {
							full = true
						}
					}
					return
				}
				for _, arg := range prev {
					gen(append(prefix, arg))
					if full {
						return
					}
				}
			}
			gen(nil)
			if full {
				break
			}
		}
		all = append(all, cur...)
		prev = append(prev, cur...)
		if full {
			return all
		}
	}
	return all
}

// Random generates n pseudo-random values of bounded height from a seed,
// so runs are reproducible.
func Random(ctors map[string]int, order []string, seed int64, n, maxDepth int) []*runtime.Value {
	r := rand.New(rand.NewSource(seed))
	pool := LitPool()
	var nullary []string
	for _, name := range order {
		if ctors[name] == 0 {
			nullary = append(nullary, name)
		}
	}
	var gen func(depth int) *runtime.Value
	gen = func(depth int) *runtime.Value {
		if depth <= 0 {
			total := len(pool) + len(nullary)
			k := r.Intn(total)
			if k < len(pool) {
				return runtime.Lit(pool[k])
			}
			return runtime.Ctor(nullary[k-len(pool)])
		}
		k := r.Intn(len(order) + len(pool))
		if k >= len(order) {
			return runtime.Lit(pool[r.Intn(len(pool))])
		}
		name := order[k]
		args := make([]*runtime.Value, ctors[name])
		for i := range args {
			args[i] = gen(depth - 1)
		}
		return runtime.Ctor(name, args...)
	}
	out := make([]*runtime.Value, 0, n)
	for len(out) < n {
		out = append(out, gen(maxDepth))
	}
	return out
}

func canonical(v *runtime.Value) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "<invalid>"
	}
	return string(b)
}
