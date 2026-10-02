package ber

// frame is one open constructed value on the parser stack.
type frame struct {
	node       *Node
	indefinite bool
	end        int // absolute end offset for definite frames; -1 for indefinite
}

// parser is the decode state machine. It walks the input left to right,
// pushing a frame for every constructed value and popping frames as their
// content is exhausted (definite) or terminated by EOC (indefinite).
type parser struct {
	data  []byte
	lim   Limits
	off   int
	stack []*frame
	nodes int
}

// Decode parses exactly one TLV value from the front of data and returns the
// node and the number of octets consumed. Trailing data is not an error here;
// use DecodeAll to require full consumption.
func Decode(data []byte, lim Limits) (*Node, int, *Error) {
	lim = lim.WithDefaults()
	if len(data) > lim.MaxInputBytes {
		return nil, 0, errf(CatResource, lim.MaxInputBytes,
			"input of %d octets exceeds limit %d", len(data), lim.MaxInputBytes)
	}
	p := &parser{data: data, lim: lim}
	root, err := p.run()
	if err != nil {
		return nil, 0, err
	}
	return root, p.off, nil
}

// DecodeAll parses data and requires that it contain exactly one TLV value
// with no trailing octets.
func DecodeAll(data []byte, lim Limits) (*Node, *Error) {
	root, consumed, err := Decode(data, lim)
	if err != nil {
		return nil, err
	}
	if consumed != len(data) {
		return nil, errf(CatSyntax, consumed,
			"%d trailing octets after top-level value", len(data)-consumed)
	}
	return root, nil
}

// run executes the state machine until the top-level value is complete.
func (p *parser) run() (*Node, *Error) {
	var root *Node
	for {
		p.closeDefiniteFrames()
		if err := p.checkOverrun(); err != nil {
			return nil, err
		}
		if len(p.stack) == 0 && root != nil {
			return root, nil
		}
		if p.off >= len(p.data) {
			if len(p.stack) == 0 {
				return nil, errf(CatTruncation, p.off, "empty input")
			}
			top := p.stack[len(p.stack)-1]
			if top.indefinite {
				return nil, errf(CatTruncation, p.off,
					"indefinite-length value at offset %d is unterminated (missing EOC)",
					top.node.Offset)
			}
			return nil, errf(CatTruncation, p.off,
				"content of value at offset %d truncated: need %d more octets",
				top.node.Offset, top.end-p.off)
		}
		node, isEOC, err := p.readHeader()
		if err != nil {
			return nil, err
		}
		if isEOC {
			if err := p.closeIndefiniteFrame(node); err != nil {
				return nil, err
			}
			continue
		}
		if err := p.attach(node, &root); err != nil {
			return nil, err
		}
	}
}

// closeDefiniteFrames pops every definite-length frame whose content ends at
// the current offset.
func (p *parser) closeDefiniteFrames() {
	for len(p.stack) > 0 {
		top := p.stack[len(p.stack)-1]
		if top.indefinite || p.off != top.end {
			return
		}
		p.stack = p.stack[:len(p.stack)-1]
	}
}

// checkOverrun detects content that ran past the end of an enclosing
// definite-length value. This can only happen through an indefinite-length
// child, whose own children are not bounded by the grandparent's declared
// end at attach time.
func (p *parser) checkOverrun() *Error {
	for i := len(p.stack) - 1; i >= 0; i-- {
		f := p.stack[i]
		if !f.indefinite && p.off > f.end {
			return errf(CatSyntax, f.end,
				"content overruns enclosing value at offset %d by %d octets",
				f.node.Offset, p.off-f.end)
		}
	}
	return nil
}

// readHeader parses the identifier and length octets at the current offset
// and advances past them. EOC (00 00) is reported via isEOC instead of a node.
func (p *parser) readHeader() (node *Node, isEOC bool, err *Error) {
	start := p.off
	t, err := parseTag(p.data, start, p.lim)
	if err != nil {
		return nil, false, err
	}
	l, err := parseLength(p.data, start+t.size, p.lim)
	if err != nil {
		return nil, false, err
	}
	p.off = start + t.size + l.size

	if t.class == Universal && t.number == TagEOC {
		// EOC is only ever the primitive pair 00 00. Anything else wearing
		// tag 0 is a protocol violation, never payload data.
		if t.constructed {
			return nil, false, errf(CatSyntax, start,
				"tag 0 with constructed bit set is reserved")
		}
		if l.indefinite || l.n != 0 {
			return nil, false, errf(CatEOC, start,
				"EOC with non-zero or indefinite length")
		}
		return nil, true, nil
	}

	if l.indefinite && !t.constructed {
		return nil, false, errf(CatSyntax, start+t.size,
			"indefinite length on primitive encoding")
	}
	if l.indefinite && !p.lim.AllowIndefinite {
		return nil, false, errf(CatIndefinite, start+t.size,
			"indefinite-length form disabled by limits")
	}

	node = &Node{
		Class:       t.class,
		Constructed: t.constructed,
		Tag:         t.number,
		Offset:      start,
		HeaderLen:   t.size + l.size,
		ContentLen:  l.n,
		Indefinite:  l.indefinite,
		MinimalLen:  l.minimal,
	}
	return node, false, nil
}

// attach links a freshly read node into the tree and, for constructed nodes,
// opens a frame. It enforces depth, node-count and containment limits.
func (p *parser) attach(node *Node, root **Node) *Error {
	p.nodes++
	if p.nodes > p.lim.MaxNodes {
		return errf(CatResource, node.Offset,
			"node count exceeds limit %d", p.lim.MaxNodes)
	}
	depth := len(p.stack) + 1
	if depth > p.lim.MaxDepth {
		return errf(CatResource, node.Offset,
			"nesting depth %d exceeds limit %d", depth, p.lim.MaxDepth)
	}
	if len(p.stack) > 0 {
		top := p.stack[len(p.stack)-1]
		if !top.indefinite && node.End() > top.end {
			return errf(CatSyntax, node.Offset,
				"value extends %d octets beyond enclosing content ending at %d",
				node.End()-top.end, top.end)
		}
		top.node.Children = append(top.node.Children, node)
	} else {
		*root = node
	}

	if !node.Constructed {
		end := node.Offset + node.HeaderLen + node.ContentLen
		if end > len(p.data) {
			return errf(CatTruncation, len(p.data),
				"content truncated: declared %d octets at offset %d, only %d remain",
				node.ContentLen, node.Offset+node.HeaderLen,
				len(p.data)-node.Offset-node.HeaderLen)
		}
		node.Content = p.data[node.Offset+node.HeaderLen : end]
		p.off = end
		return nil
	}

	if node.Indefinite {
		p.stack = append(p.stack, &frame{node: node, indefinite: true, end: -1})
		return nil
	}
	end := node.Offset + node.HeaderLen + node.ContentLen
	if end > len(p.data) {
		return errf(CatTruncation, len(p.data),
			"constructed content truncated: declared %d octets at offset %d, only %d remain",
			node.ContentLen, node.Offset+node.HeaderLen,
			len(p.data)-node.Offset-node.HeaderLen)
	}
	p.stack = append(p.stack, &frame{node: node, end: end})
	return nil
}

// closeIndefiniteFrame consumes an EOC. The EOC is valid only as the
// terminator of the innermost open indefinite-length value.
func (p *parser) closeIndefiniteFrame(eoc *Node) *Error {
	if len(p.stack) == 0 || !p.stack[len(p.stack)-1].indefinite {
		return errf(CatEOC, p.off-2,
			"EOC without enclosing indefinite-length constructed value")
	}
	p.stack = p.stack[:len(p.stack)-1]
	return nil
}
