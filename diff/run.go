package diff

import (
	"encoding/json"
	"fmt"
	"pmd/frontend"
	"pmd/ir"
	"pmd/runtime"
)

// Mismatch records one value where the two engines disagree.
type Mismatch struct {
	Value  json.RawMessage `json:"value"`
	Reason string          `json:"reason"`
	Tree   string          `json:"tree"`
	Seq    string          `json:"sequential"`
}

// Uncertain records one value for which no verdict could be given
// (e.g. it failed validation and could not be evaluated meaningfully).
type Uncertain struct {
	Value  json.RawMessage `json:"value"`
	Reason string          `json:"reason"`
}

// Report is the outcome of a differential run.
type Report struct {
	Total      int         `json:"total"`
	Compared   int         `json:"compared"`
	Equal      int         `json:"equal"`
	Mismatches []Mismatch  `json:"mismatches,omitempty"`
	Uncertain  []Uncertain `json:"uncertain,omitempty"`
}

// OK reports whether no mismatches were found.
func (r *Report) OK() bool { return len(r.Mismatches) == 0 }

// Run evaluates both engines over the given values and compares them.
// Values that fail validation are listed as uncertain rather than compared.
func Run(prog *frontend.Program, values []*runtime.Value) *Report {
	tree := ir.Compile(prog)
	rep := &Report{Total: len(values)}
	for _, v := range values {
		raw, _ := json.Marshal(v)
		if f := v.Validate(prog.Ctors); f != nil {
			rep.Uncertain = append(rep.Uncertain, Uncertain{
				Value:  raw,
				Reason: fmt.Sprintf("invalid value at %s: %s", f.Path, f.Detail),
			})
			continue
		}
		a := runtime.EvalTree(tree, v)
		b := runtime.EvalSequential(prog, v)
		rep.Compared++
		ok, reason := CompareOutcomes(a, b)
		if ok {
			rep.Equal++
			continue
		}
		rep.Mismatches = append(rep.Mismatches, Mismatch{
			Value:  raw,
			Reason: reason,
			Tree:   Summarize(a),
			Seq:    Summarize(b),
		})
	}
	return rep
}
