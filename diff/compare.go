package diff

import (
	"fmt"
	"pmd/runtime"
	"strings"
)

// CompareOutcomes checks that two engines agree on everything observable:
// match status, winning branch, label, bindings, effect trace and failure
// category. Step traces are engine-specific and intentionally not compared.
func CompareOutcomes(a, b runtime.Outcome) (bool, string) {
	if (a.Failure == nil) != (b.Failure == nil) {
		return false, fmt.Sprintf("failure presence differs (tree=%v seq=%v)",
			failureCat(a.Failure), failureCat(b.Failure))
	}
	if a.Failure != nil && a.Failure.Category != b.Failure.Category {
		return false, fmt.Sprintf("failure category differs (tree=%s seq=%s)",
			a.Failure.Category, b.Failure.Category)
	}
	if a.Matched != b.Matched {
		return false, fmt.Sprintf("matched differs (tree=%v seq=%v)", a.Matched, b.Matched)
	}
	if a.Matched {
		if a.Branch != b.Branch {
			return false, fmt.Sprintf("branch differs (tree=%d seq=%d)", a.Branch, b.Branch)
		}
		if a.Label != b.Label {
			return false, fmt.Sprintf("label differs (tree=%q seq=%q)", a.Label, b.Label)
		}
		if !bindingsEqual(a.Bindings, b.Bindings) {
			return false, fmt.Sprintf("bindings differ (tree=%s seq=%s)",
				formatBindings(a.Bindings), formatBindings(b.Bindings))
		}
	}
	if !effectsEqual(a.Effects, b.Effects) {
		return false, fmt.Sprintf("effects differ (tree=%s seq=%s)",
			formatEffects(a.Effects), formatEffects(b.Effects))
	}
	return true, ""
}

func failureCat(f *runtime.Failure) string {
	if f == nil {
		return "<none>"
	}
	return f.Category
}

func bindingsEqual(a, b map[string]*runtime.Value) bool {
	if len(a) != len(b) {
		return false
	}
	for k, va := range a {
		vb, ok := b[k]
		if !ok || !runtime.EqualValue(va, vb) {
			return false
		}
	}
	return true
}

func effectsEqual(a, b []runtime.Effect) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Label != b[i].Label || a[i].Value != b[i].Value {
			return false
		}
	}
	return true
}

func formatBindings(m map[string]*runtime.Value) string {
	parts := make([]string, 0, len(m))
	for k, v := range m {
		parts = append(parts, k+"="+v.String())
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

func formatEffects(effs []runtime.Effect) string {
	parts := make([]string, len(effs))
	for i, e := range effs {
		parts[i] = e.Label + "=" + e.Value.String()
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

// Summarize renders an outcome on one line for reports.
func Summarize(o runtime.Outcome) string {
	if o.Failure != nil {
		return "failure=" + o.Failure.Category + " (" + o.Failure.Detail + ")"
	}
	if !o.Matched {
		return "no-match"
	}
	return fmt.Sprintf("branch=%d label=%q bindings=%s effects=%s",
		o.Branch, o.Label, formatBindings(o.Bindings), formatEffects(o.Effects))
}
