package runtime

import (
	"encoding/json"
	"fmt"
	"os"

	"scopelang/internal/diag"
)

// Injection is one failure-injection rule. Point uses the stable frontend
// id (init@N / fail@N / close@N). Instance > 0 restricts the rule to a
// specific repeat iteration (1-based) for in-loop points; instance 0 means
// every iteration. When both a per-iteration and a generic rule exist, the
// exact (point, instance) rule wins.
type Injection struct {
	Point    string        `json:"point"`
	Instance int           `json:"instance,omitempty"`
	Category diag.Category `json:"category"`
	Code     string        `json:"code,omitempty"`
	Message  string        `json:"message,omitempty"`
}

// RunSpec is the full configuration of one replayable run.
type RunSpec struct {
	ID         string      `json:"id"`
	Program    string      `json:"program"`
	Injections []Injection `json:"injections"`
}

func LoadSpec(path string) (*RunSpec, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, diag.New(diag.Input, "SPEC_READ",
			fmt.Sprintf("cannot read run spec %s: %v", path, err)).
			With(diag.PhaseConfig, "", 0)
	}
	var spec RunSpec
	if err := json.Unmarshal(data, &spec); err != nil {
		return nil, diag.New(diag.Input, "SPEC_JSON",
			fmt.Sprintf("invalid run spec JSON: %v", err)).With(diag.PhaseConfig, "", 0)
	}
	if err := ValidateSpec(&spec); err != nil {
		return nil, err
	}
	return &spec, nil
}

func ValidateSpec(s *RunSpec) error {
	if s == nil {
		return diag.New(diag.Input, "SPEC_EMPTY", "missing run spec").With(diag.PhaseConfig, "", 0)
	}
	if s.Program == "" {
		return diag.New(diag.Input, "SPEC_NO_PROGRAM", "run spec has no program path/source marker").
			With(diag.PhaseConfig, "", 0)
	}
	seen := map[string]bool{}
	for _, inj := range s.Injections {
		if inj.Point == "" {
			return diag.New(diag.Input, "SPEC_BAD_POINT", "injection without point").
				With(diag.PhaseConfig, "", 0)
		}
		if !validPoint(inj.Point) {
			return diag.New(diag.Input, "SPEC_BAD_POINT",
				fmt.Sprintf("invalid point %q (want init@N/fail@N/close@N)", inj.Point)).
				With(diag.PhaseConfig, "", 0)
		}
		if inj.Instance < 0 {
			return diag.New(diag.Input, "SPEC_BAD_INSTANCE",
				fmt.Sprintf("negative instance for %s", inj.Point)).
				With(diag.PhaseConfig, "", 0)
		}
		switch inj.Category {
		case diag.Input, diag.StateConflict, diag.ResourceExhausted, diag.Computation:
		default:
			return diag.New(diag.Input, "SPEC_BAD_CATEGORY",
				fmt.Sprintf("invalid category %q for %s", inj.Category, inj.Point)).
				With(diag.PhaseConfig, inj.Point, inj.Instance)
		}
		key := fmt.Sprintf("%s#%d", inj.Point, inj.Instance)
		if seen[key] {
			return diag.New(diag.StateConflict, "SPEC_DUP_INJECTION",
				fmt.Sprintf("duplicate injection for %s", key)).
				With(diag.PhaseConfig, inj.Point, inj.Instance)
		}
		seen[key] = true
	}
	return nil
}

func validPoint(p string) bool {
	if len(p) < 6 {
		return false
	}
	var prefix string
	switch {
	case startsWith(p, "init@"):
		prefix = "init@"
	case startsWith(p, "fail@"):
		prefix = "fail@"
	case startsWith(p, "close@"):
		prefix = "close@"
	default:
		return false
	}
	rest := p[len(prefix):]
	if rest == "" {
		return false
	}
	for i := 0; i < len(rest); i++ {
		if rest[i] < '0' || rest[i] > '9' {
			return false
		}
	}
	return rest[0] != '0'
}

func startsWith(s, p string) bool { return len(s) >= len(p) && s[:len(p)] == p }
