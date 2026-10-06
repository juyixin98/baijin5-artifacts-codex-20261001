package runtime

import "pmd/frontend"

// Effect is one recorded guard side effect.
type Effect struct {
	Label string            `json:"label"`
	Value frontend.LitValue `json:"value"`
}

// Failure is a categorized evaluation failure.
type Failure struct {
	Category string `json:"category"`
	Detail   string `json:"detail"`
	Path     string `json:"path,omitempty"`
}

// Outcome is the result of matching one value against one program.
// Exactly one of Matched/Failure describes the final state; Steps is a
// human-readable trace of the key evaluation steps.
type Outcome struct {
	Matched  bool              `json:"matched"`
	Branch   int               `json:"branch"`
	Label    string            `json:"label,omitempty"`
	Bindings map[string]*Value `json:"bindings,omitempty"`
	Effects  []Effect          `json:"effects,omitempty"`
	Failure  *Failure          `json:"failure,omitempty"`
	Steps    []string          `json:"steps"`
}
