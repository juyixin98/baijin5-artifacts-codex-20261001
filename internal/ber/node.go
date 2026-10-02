package ber

// Node is one decoded TLV value. Primitive values carry Content; constructed
// values carry Children.
type Node struct {
	Class       Class  `json:"-"`
	Constructed bool   `json:"-"`
	Tag         uint64 `json:"-"`

	// Offset is the position of the first identifier octet in the input.
	Offset int `json:"-"`
	// HeaderLen is identifier + length octets.
	HeaderLen int `json:"-"`
	// ContentLen is the declared content length (0 for indefinite form).
	ContentLen int `json:"-"`
	// Indefinite records that the value used BER indefinite-length form.
	Indefinite bool `json:"-"`
	// MinimalLen records that the length field used the shortest form.
	MinimalLen bool `json:"-"`

	// Content holds the raw content octets of a primitive value.
	Content []byte `json:"-"`
	// Children holds the child values of a constructed value.
	Children []*Node `json:"-"`
}

// End returns the offset one past the last octet of this node in the input.
func (n *Node) End() int {
	if n.Constructed {
		end := n.Offset + n.HeaderLen
		for _, c := range n.Children {
			end = c.End()
		}
		if n.Indefinite {
			end += 2 // EOC
		}
		return end
	}
	return n.Offset + n.HeaderLen + n.ContentLen
}

// Is reports whether the node matches a class and tag number.
func (n *Node) Is(class Class, number uint64) bool {
	return n.Class == class && n.Tag == number
}
