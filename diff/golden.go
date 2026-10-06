package diff

import (
	"encoding/json"
	"fmt"
	"pmd/frontend"
	"pmd/ir"
	"pmd/runtime"
)

// GoldenCase is one hand-written expectation. Only the fields that are
// set are checked, so fixtures can stay focused.
type GoldenCase struct {
	Name   string          `json:"name"`
	Value  json.RawMessage `json:"value"`
	Expect ExpectedOutcome `json:"expect"`
}

// ExpectedOutcome is the checkable part of an outcome.
type ExpectedOutcome struct {
	Matched  *bool                      `json:"matched,omitempty"`
	Branch   *int                       `json:"branch,omitempty"`
	Label    string                     `json:"label,omitempty"`
	Failure  string                     `json:"failure,omitempty"`
	Bindings map[string]json.RawMessage `json:"bindings,omitempty"`
	Effects  []string                   `json:"effects,omitempty"` // "label=value" entries
}

// CheckGolden evaluates the tree engine against hand-written golden cases
// and returns one problem string per deviation (empty means all passed).
// The expectations come from fixture files, not from the implementation.
func CheckGolden(prog *frontend.Program, cases []GoldenCase) []string {
	tree := ir.Compile(prog)
	var problems []string
	for _, gc := range cases {
		prefix := gc.Name
		var v runtime.Value
		if err := json.Unmarshal(gc.Value, &v); err != nil {
			problems = append(problems, fmt.Sprintf("%s: bad value JSON: %v", prefix, err))
			continue
		}
		o := runtime.EvalTree(tree, &v)
		exp := gc.Expect
		if exp.Matched != nil && o.Matched != *exp.Matched {
			problems = append(problems, fmt.Sprintf("%s: matched=%v, want %v", prefix, o.Matched, *exp.Matched))
		}
		if exp.Branch != nil && o.Branch != *exp.Branch {
			problems = append(problems, fmt.Sprintf("%s: branch=%d, want %d", prefix, o.Branch, *exp.Branch))
		}
		if exp.Label != "" && o.Label != exp.Label {
			problems = append(problems, fmt.Sprintf("%s: label=%q, want %q", prefix, o.Label, exp.Label))
		}
		if exp.Failure != "" {
			if o.Failure == nil {
				problems = append(problems, fmt.Sprintf("%s: no failure, want category %q", prefix, exp.Failure))
			} else if o.Failure.Category != exp.Failure {
				problems = append(problems, fmt.Sprintf("%s: failure=%q, want %q", prefix, o.Failure.Category, exp.Failure))
			}
		}
		for name, raw := range exp.Bindings {
			got, ok := o.Bindings[name]
			if !ok {
				problems = append(problems, fmt.Sprintf("%s: binding %q missing", prefix, name))
				continue
			}
			var want runtime.Value
			if err := json.Unmarshal(raw, &want); err != nil {
				problems = append(problems, fmt.Sprintf("%s: binding %q has bad JSON: %v", prefix, name, err))
				continue
			}
			if !runtime.EqualValue(got, &want) {
				problems = append(problems, fmt.Sprintf("%s: binding %q = %s, want %s", prefix, name, got, &want))
			}
		}
		if exp.Effects != nil {
			got := make([]string, len(o.Effects))
			for i, e := range o.Effects {
				got[i] = e.Label + "=" + e.Value.String()
			}
			if !equalStrings(got, exp.Effects) {
				problems = append(problems, fmt.Sprintf("%s: effects=%v, want %v", prefix, got, exp.Effects))
			}
		}
	}
	return problems
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
