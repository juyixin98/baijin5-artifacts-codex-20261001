// Package ir compiles validated match programs into shared-test decision
// trees. A path test performed on the way down the tree is never repeated
// on the same route, so overlapping branches share their common tests.
package ir

import (
	"fmt"
	"strings"

	"pmd/frontend"
)

// Version is the module version reported by the service layer.
const Version = "0.1.0"

// Path locates a sub-value: the argument indices to follow from the root.
// The empty path ("$") is the scrutinee itself.
type Path []int

func (p Path) String() string {
	var b strings.Builder
	b.WriteByte('$')
	for _, i := range p {
		fmt.Fprintf(&b, ".%d", i)
	}
	return b.String()
}

// Kind discriminates decision-tree nodes.
type Kind string

const (
	Switch Kind = "switch"
	GuardN Kind = "guard"
	Leaf   Kind = "leaf"
	Fail   Kind = "fail"
)

// TestKey is one switch arm selector: a constructor name or a literal.
type TestKey struct {
	Ctor string             `json:"ctor,omitempty"`
	Lit  *frontend.LitValue `json:"lit,omitempty"`
}

func (k TestKey) String() string {
	if k.Ctor != "" {
		return k.Ctor
	}
	if k.Lit != nil {
		return k.Lit.String()
	}
	return "?"
}

// Case is one arm of a switch node.
type Case struct {
	Test TestKey `json:"test"`
	Node *Node   `json:"node"`
}

// Binding associates a variable name with the path it is bound at.
type Binding struct {
	Name string `json:"name"`
	Path Path   `json:"path"`
}

// Node is a decision-tree node. Which fields are set depends on Kind:
//
//	Switch: Path, Cases, Default
//	GuardN: Expr, ExprText, Bindings, Then, Else
//	Leaf:   Branch, Label, Bindings
//	Fail:   (none)
type Node struct {
	ID   int  `json:"id"`
	Kind Kind `json:"kind"`

	Path    Path   `json:"path,omitempty"`
	Cases   []Case `json:"cases,omitempty"`
	Default *Node  `json:"default,omitempty"`

	Expr     frontend.Expr `json:"-"`
	ExprText string        `json:"expr,omitempty"`
	Then     *Node         `json:"then,omitempty"`
	Else     *Node         `json:"else,omitempty"`

	Bindings []Binding `json:"bindings,omitempty"`
	Branch   int       `json:"branch,omitempty"`
	Label    string    `json:"label,omitempty"`
}

// Stats summarizes a compiled tree.
type Stats struct {
	Nodes    int `json:"nodes"`
	Switches int `json:"switches"`
	Guards   int `json:"guards"`
	Leaves   int `json:"leaves"`
	Fails    int `json:"fails"`
	MaxDepth int `json:"max_depth"`
}

// Tree is a compiled match program.
type Tree struct {
	Root     *Node          `json:"root"`
	Ctors    map[string]int `json:"ctors"`
	Stats    Stats          `json:"stats"`
	Warnings []string       `json:"warnings,omitempty"`
}

func computeStats(root *Node) Stats {
	var s Stats
	var walk func(n *Node, depth int)
	walk = func(n *Node, depth int) {
		if n == nil {
			return
		}
		s.Nodes++
		if depth > s.MaxDepth {
			s.MaxDepth = depth
		}
		switch n.Kind {
		case Switch:
			s.Switches++
			for _, c := range n.Cases {
				walk(c.Node, depth+1)
			}
			walk(n.Default, depth+1)
		case GuardN:
			s.Guards++
			walk(n.Then, depth+1)
			walk(n.Else, depth+1)
		case Leaf:
			s.Leaves++
		case Fail:
			s.Fails++
		}
	}
	walk(root, 1)
	return s
}
