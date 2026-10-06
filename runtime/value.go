// Package runtime interprets compiled decision trees and also provides an
// independent sequential pattern interpreter used as the semantic
// reference for differential testing.
package runtime

import (
	"encoding/json"
	"fmt"

	"pmd/frontend"
)

// Version is the module version reported by the service layer.
const Version = "0.1.0"

// Failure categories produced by this module.
const (
	CatInvalidValue = "invalid_value"
	CatNoMatch      = "no_match"
	CatGuardError   = "guard_error"
	CatInternal     = "internal_error"
)

// VKind discriminates runtime values.
type VKind string

const (
	VLit  VKind = "lit"
	VCtor VKind = "ctor"
)

// Value is an algebraic data value: a literal or a constructor application.
type Value struct {
	Kind VKind
	Lit  frontend.LitValue
	Ctor string
	Args []*Value
}

// Lit builds a literal value.
func Lit(v frontend.LitValue) *Value { return &Value{Kind: VLit, Lit: v} }

// Ctor builds a constructor value.
func Ctor(name string, args ...*Value) *Value {
	return &Value{Kind: VCtor, Ctor: name, Args: args}
}

func (v *Value) String() string {
	if v.Kind == VLit {
		return v.Lit.String()
	}
	if len(v.Args) == 0 {
		return v.Ctor
	}
	s := v.Ctor + "("
	for i, a := range v.Args {
		if i > 0 {
			s += ", "
		}
		s += a.String()
	}
	return s + ")"
}

// MarshalJSON renders a value as {"lit": ...} or {"ctor": ..., "args": [...]}.
func (v *Value) MarshalJSON() ([]byte, error) {
	if v.Kind == VLit {
		return json.Marshal(struct {
			Lit frontend.LitValue `json:"lit"`
		}{v.Lit})
	}
	return json.Marshal(struct {
		Ctor string   `json:"ctor"`
		Args []*Value `json:"args,omitempty"`
	}{v.Ctor, v.Args})
}

// UnmarshalJSON parses the {"lit"/"ctor"} value encoding.
func (v *Value) UnmarshalJSON(data []byte) error {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return fmt.Errorf("value must be a JSON object: %w", err)
	}
	if litRaw, ok := raw["lit"]; ok {
		var l frontend.LitValue
		if err := json.Unmarshal(litRaw, &l); err != nil {
			return err
		}
		*v = Value{Kind: VLit, Lit: l}
		return nil
	}
	if ctorRaw, ok := raw["ctor"]; ok {
		var name string
		if err := json.Unmarshal(ctorRaw, &name); err != nil {
			return fmt.Errorf("ctor must be a string: %w", err)
		}
		var args []*Value
		if argsRaw, ok := raw["args"]; ok {
			if err := json.Unmarshal(argsRaw, &args); err != nil {
				return fmt.Errorf("args must be an array: %w", err)
			}
		}
		*v = Value{Kind: VCtor, Ctor: name, Args: args}
		return nil
	}
	return fmt.Errorf("value object must have a \"lit\" or \"ctor\" field")
}

// Validate checks a value against the constructor table (known
// constructors, matching arities) and returns a categorized failure.
func (v *Value) Validate(ctors map[string]int) *Failure {
	var walk func(v *Value, path string) *Failure
	walk = func(v *Value, path string) *Failure {
		if v == nil {
			return &Failure{Category: CatInvalidValue, Detail: "missing value", Path: path}
		}
		if v.Kind == VLit {
			return nil
		}
		arity, ok := ctors[v.Ctor]
		if !ok {
			return &Failure{Category: CatInvalidValue, Detail: fmt.Sprintf("unknown constructor %q", v.Ctor), Path: path}
		}
		if len(v.Args) != arity {
			return &Failure{Category: CatInvalidValue, Detail: fmt.Sprintf("constructor %q expects %d argument(s), got %d", v.Ctor, arity, len(v.Args)), Path: path}
		}
		for i, a := range v.Args {
			if f := walk(a, fmt.Sprintf("%s.%d", path, i)); f != nil {
				return f
			}
		}
		return nil
	}
	return walk(v, "$")
}

// EqualValue reports deep structural equality.
func EqualValue(a, b *Value) bool {
	if a == nil || b == nil {
		return a == b
	}
	if a.Kind != b.Kind {
		return false
	}
	if a.Kind == VLit {
		return a.Lit == b.Lit
	}
	if a.Ctor != b.Ctor || len(a.Args) != len(b.Args) {
		return false
	}
	for i := range a.Args {
		if !EqualValue(a.Args[i], b.Args[i]) {
			return false
		}
	}
	return true
}
