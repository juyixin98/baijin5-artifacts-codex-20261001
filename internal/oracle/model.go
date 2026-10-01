// Package oracle is an INDEPENDENT reference implementation used to check the
// ScopeLang toolchain. It deliberately imports none of the project's core
// packages (no parser/ir/lower/runtime): its input is a hand-authored JSON
// model and its interpreter, host and cleanup policy are written separately.
//
// Keeping the oracle independent prevents "the program being graded by
// itself": golden expectations are authored in the model language and
// simulated here, then compared with outputs of the real toolchain.
//
// Model JSON (one file = one scenario):
//
//	{
//	  "id": "...",
//	  "program": [ <stmt> ... ],
//	  "injections": [ {"point":"init@1","category":"ResourceExhausted"} ]
//	}
//
// Statements are tagged objects:
//   {"t":"acquire","ord":1,"name":"a","cleanup":[<emit|fail>...]}
//   {"t":"scope","body":[...]}
//   {"t":"repeat","count":3,"body":[...]}
//   {"t":"emit","text":"..."}
//   {"t":"fail","ord":2}            // explicit fail@2
//   {"t":"break"}
//   {"t":"return","value":"v"}
//
// Events use the same textual vocabulary as the runtime ("acquire_start",
// "acquire_success", "release_start", "release_done", "emit") so traces can
// be compared directly. Failure points are init@N / close@N / fail@N with an
// optional "#instance" suffix inside repeats.
package oracle

import (
	"encoding/json"
	"fmt"
)

type Node struct {
	T       string   `json:"t"`
	Ord     int      `json:"ord,omitempty"`
	Name    string   `json:"name,omitempty"`
	Text    string   `json:"text,omitempty"`
	Value   string   `json:"value,omitempty"`
	Count   int      `json:"count,omitempty"`
	Body    []*Node  `json:"body,omitempty"`
	Cleanup []*Node  `json:"cleanup,omitempty"`
}

type Injection struct {
	Point    string `json:"point"`
	Instance int    `json:"instance,omitempty"`
	Category string `json:"category"`
	Code     string `json:"code,omitempty"`
	Message  string `json:"message,omitempty"`
}

type Case struct {
	ID         string      `json:"id"`
	Program    []*Node     `json:"program"`
	Injections []Injection `json:"injections"`
}

func DecodeCase(data []byte) (*Case, error) {
	var c Case
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, fmt.Errorf("oracle case JSON: %w", err)
	}
	if err := c.validate(); err != nil {
		return nil, err
	}
	return &c, nil
}

func (c *Case) validate() error {
	if c.ID == "" {
		return fmt.Errorf("oracle case without id")
	}
	seen := map[string]bool{}
	for _, inj := range c.Injections {
		if inj.Point == "" {
			return fmt.Errorf("injection without point")
		}
		switch inj.Category {
		case "InputError", "StateConflict", "ResourceExhausted", "ComputationFailure":
		default:
			return fmt.Errorf("injection %s has bad category %q", inj.Point, inj.Category)
		}
		key := fmt.Sprintf("%s#%d", inj.Point, inj.Instance)
		if seen[key] {
			return fmt.Errorf("duplicate injection %s", key)
		}
		seen[key] = true
	}
	var walk func(ns []*Node) error
	walk = func(ns []*Node) error {
		for _, n := range ns {
			switch n.T {
			case "acquire":
				if n.Ord <= 0 || n.Name == "" {
					return fmt.Errorf("bad acquire node: %+v", n)
				}
				for _, cl := range n.Cleanup {
					if cl.T != "emit" && cl.T != "fail" {
						return fmt.Errorf("cleanup allows emit/fail only, got %q", cl.T)
					}
				}
			case "scope":
				if err := walk(n.Body); err != nil {
					return err
				}
			case "repeat":
				if n.Count < 0 {
					return fmt.Errorf("negative repeat count")
				}
				if err := walk(n.Body); err != nil {
					return err
				}
			case "emit", "break", "return":
			case "fail":
				if n.Ord <= 0 {
					return fmt.Errorf("fail node needs ord")
				}
			default:
				return fmt.Errorf("unknown node type %q", n.T)
			}
		}
		return nil
	}
	return walk(c.Program)
}
