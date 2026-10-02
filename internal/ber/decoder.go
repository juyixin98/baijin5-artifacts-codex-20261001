package ber

import (
	"fmt"
)

type decoder struct {
	b      []byte
	lim    Limits
	tracer Tracer
}

type options struct {
	Limits Limits
	Tracer Tracer
}

// Decode performs a structural BER decode of a single top-level value.
// It does not enforce type-specific content rules; call [Node.ValidateBER]
// (or [DecodeAndValidate]) for those.
func Decode(data []byte, limits Limits) (*Node, error) {
	node, derr := decode(data, options{Limits: limits})
	if derr != nil {
		return nil, derr
	}
	return node, nil
}

// DecodeTraced is Decode with a progress tracer for diagnostics.
func DecodeTraced(data []byte, limits Limits, tracer Tracer) (*Node, error) {
	node, derr := decode(data, options{Limits: limits, Tracer: tracer})
	if derr != nil {
		return nil, derr
	}
	return node, nil
}

// DecodeAndValidate decodes and immediately validates against the profile.
// mode is "BER" (structural + content rules) or "DER" (adds canonical rules).
func DecodeAndValidate(data []byte, mode string, limits Limits) (*Node, error) {
	node, derr := Decode(data, limits)
	if derr != nil {
		return nil, derr
	}
	var verr error
	if mode == "DER" {
		verr = node.ValidateDER(limits)
	} else {
		verr = node.ValidateBER(limits)
	}
	if verr != nil {
		return node, verr
	}
	return node, nil
}

func decode(data []byte, opt options) (*Node, *DecodeError) {
	d := &decoder{b: data, lim: opt.Limits.normalized(), tracer: opt.Tracer}
	if d.tracer != nil {
		d.tracer(Step{Name: "start", Offset: 0, Detail: fmt.Sprintf("%d bytes, limits=%+v", len(data), d.lim)})
	}
	if p, kind := d.leadingEOC(0, false); kind != eocNone {
		if kind == eocTruncated {
			return nil, d.err(KindMalformedEOC, int64(p), 0,
				"lone 0x00 at top level is not a value (truncated/misplaced EOC)")
		}
		return nil, d.err(KindMalformedEOC, int64(p), 0,
			"EOC terminates no indefinite-length construction at top level")
	}
	node, np, err := d.parseValue(0, 1)
	if err != nil {
		return nil, err
	}
	if np < len(data) {
		// Remaining 00 00 is an EOC with no matching construction, not
		// ordinary trailing data: it must never be swallowed as a value.
		if ep, kind := d.leadingEOC(np, false); kind == eocPair {
			return nil, d.err(KindMalformedEOC, int64(ep), 0,
				"EOC terminates no indefinite-length construction at top level")
		}
		return nil, d.err(KindTrailingData, int64(np), 0, "bytes remain after the single top-level value")
	}
	return node, nil
}

func (d *decoder) trace(name string, off, depth int64, detail string) {
	if d.tracer != nil {
		d.tracer(Step{Name: name, Offset: off, Depth: int(depth), Detail: detail})
	}
}

func (d *decoder) err(kind ErrorKind, off int64, depth int, msg string) *DecodeError {
	return &DecodeError{Kind: kind, Offset: off, Depth: depth, Msg: msg}
}

// eocClass classifies an 0x00 octet at position p.
type eocClass int

const (
	eocNone      eocClass = iota
	eocPair               // 00 00
	eocTruncated          // trailing lone 00
)

// leadingEOC classifies the bytes at p when they begin with 0x00.
// When inIndef is false, an 00 00 pair is a misplaced EOC; a lone final
// 00 is treated the same way (a value cannot start with tag number 0).
// When inIndef is true, a lone final 00 is a truncated terminator.
func (d *decoder) leadingEOC(p int, inIndef bool) (int, eocClass) {
	if p >= len(d.b) || d.b[p] != 0x00 {
		return p, eocNone
	}
	if p+1 >= len(d.b) {
		if inIndef {
			return p, eocTruncated
		}
		return p, eocPair // misplaced; reported as MALFORMED_EOC by caller
	}
	if d.b[p+1] == 0x00 {
		return p, eocPair
	}
	return p, eocNone // 00 xx (xx!=00): reserved tag 0, surfaced by parseIdentifier
}

// parseValue parses one TLV beginning at p. depth is 1 for the top value.
// Returns the node and the offset just past it.
func (d *decoder) parseValue(p int, depth int) (*Node, int, *DecodeError) {
	start := p
	node := &Node{Start: int64(p)}

	cls, constructed, tag, tagBytes, np, err := d.parseIdentifier(p, depth)
	if err != nil {
		return nil, 0, err
	}
	node.Class, node.Constructed, node.Tag, node.rawTag = cls, constructed, tag, tagBytes
	p = np

	indef, contentLen, lengthOff, lengthBytes, np2, err := d.parseLength(p, constructed, depth)
	if err != nil {
		return nil, 0, err
	}
	node.Indefinite, node.LengthOffset, node.rawLength = indef, int64(lengthOff), lengthBytes
	p = np2
	node.HeaderEnd = int64(p)
	d.trace("enter", int64(start), int64(depth),
		fmt.Sprintf("class=%s tag=%d constructed=%t indefinite=%t len=%d", cls, tag, constructed, indef, contentLen))

	if constructed {
		p, err = d.parseChildren(node, p, contentLen, indef, depth)
	} else {
		p, err = d.parsePrimitive(node, p, contentLen, depth)
	}
	if err != nil {
		return nil, 0, err
	}
	node.End = int64(p)
	return node, p, nil
}

func (d *decoder) parseIdentifier(p, depth int) (Class, bool, uint32, []byte, int, *DecodeError) {
	if p >= len(d.b) {
		return 0, false, 0, nil, p, d.err(KindTruncated, int64(len(d.b)), depth, "missing identifier octet")
	}
	first := d.b[p]
	cls := Class(first >> 6)
	constructed := first&0x20 != 0
	low := first & 0x1F
	if low != 0x1F {
		// Universal tag 0 is the end-of-contents tag; an 00 xx header is
		// handled by the EOC classifier before reaching here. Other classes
		// legitimately use tag number 0 (e.g. context [0] = 0x80/0xA0).
		if low == 0 && cls == ClassUniversal {
			return 0, false, 0, nil, p, d.err(KindInvalidTag, int64(p), depth, "universal tag number 0 is reserved for EOC")
		}
		d.trace("tag", int64(p), int64(depth), fmt.Sprintf("short tag=%d", low))
		return cls, constructed, uint32(low), []byte{first}, p + 1, nil
	}

	start := p
	p++
	var tag uint64
	sub := 0
	for {
		if p >= len(d.b) {
			return 0, false, 0, nil, p, d.err(KindTruncated, int64(len(d.b)), depth, "truncated long-form tag")
		}
		sub++
		if sub > d.lim.MaxTagBytes-1 {
			return 0, false, 0, nil, p, d.err(KindSizeExceeded, int64(p), depth,
				fmt.Sprintf("tag uses more than %d identifier octets", d.lim.MaxTagBytes))
		}
		oct := d.b[p]
		tag = tag<<7 | uint64(oct&0x7F)
		if tag > uint64(d.lim.MaxTagNumber) {
			return 0, false, 0, nil, p, d.err(KindSizeExceeded, int64(p), depth,
				fmt.Sprintf("tag number exceeds bound %d", d.lim.MaxTagNumber))
		}
		p++
		if oct&0x80 == 0 {
			break
		}
	}
	d.trace("tag", int64(start), int64(depth), fmt.Sprintf("long tag=%d (%d octets)", tag, sub+1))
	return cls, constructed, uint32(tag), append([]byte(nil), d.b[start:p]...), p, nil
}

func (d *decoder) parseLength(p int, constructed bool, depth int) (indef bool, contentLen int64, lengthOff int, raw []byte, next int, err *DecodeError) {
	if p >= len(d.b) {
		return false, 0, p, nil, p, d.err(KindTruncated, int64(len(d.b)), depth, "missing length octet")
	}
	lengthOff = p
	first := d.b[p]
	switch {
	case first == 0xFF:
		return false, 0, lengthOff, nil, p, d.err(KindInvalidLength, int64(p), depth, "length octet 0xFF is reserved")
	case first < 0x80:
		d.trace("length", int64(p), int64(depth), fmt.Sprintf("definite short %d", first))
		return false, int64(first), lengthOff, []byte{first}, p + 1, nil
	case first == 0x80:
		if !constructed {
			return false, 0, lengthOff, nil, p, d.err(KindInvalidLength, int64(p), depth, "indefinite length on primitive value")
		}
		d.trace("length", int64(p), int64(depth), "indefinite")
		return true, -1, lengthOff, []byte{first}, p + 1, nil
	default:
		n := int(first & 0x7F)
		if n > d.lim.MaxLengthBytes {
			return false, 0, lengthOff, nil, p, d.err(KindLengthOverflow, int64(p), depth,
				fmt.Sprintf("length-of-length %d exceeds bound %d", n, d.lim.MaxLengthBytes))
		}
		p++
		if p+n > len(d.b) {
			return false, 0, lengthOff, nil, p, d.err(KindTruncated, int64(len(d.b)), depth, "truncated long-form length")
		}
		var v int64
		for i := 0; i < n; i++ {
			v = v<<8 | int64(d.b[p+i])
		}
		// n <= MaxLengthBytes (<=5) keeps v within int64 even before the
		// deployment content bound is applied below.
		if v < 0 {
			return false, 0, lengthOff, nil, p, d.err(KindSizeExceeded, int64(lengthOff), depth, "length value out of range")
		}
		if v > int64(d.lim.MaxContentBytes) {
			return false, 0, lengthOff, nil, p, d.err(KindSizeExceeded, int64(lengthOff), depth,
				fmt.Sprintf("content length %d exceeds bound %d", v, d.lim.MaxContentBytes))
		}
		d.trace("length", int64(lengthOff), int64(depth), fmt.Sprintf("definite long %d (%d octets)", v, n))
		raw = append([]byte(nil), d.b[lengthOff:p+n]...)
		return false, v, lengthOff, raw, p + n, nil
	}
}

func (d *decoder) parsePrimitive(node *Node, p int, contentLen int64, depth int) (int, *DecodeError) {
	end := p + int(contentLen)
	if end > len(d.b) {
		return p, d.err(KindTruncated, int64(len(d.b)), depth,
			fmt.Sprintf("primitive content needs %d bytes, %d available", end-p, len(d.b)-p))
	}
	val := make([]byte, contentLen)
	copy(val, d.b[p:end])
	node.Value = val
	return end, nil
}

func (d *decoder) parseChildren(node *Node, p int, contentLen int64, indef bool, depth int) (int, *DecodeError) {
	if depth > d.lim.MaxDepth {
		return p, d.err(KindDepthExceeded, node.Start, depth,
			fmt.Sprintf("nesting depth %d exceeds bound %d", depth, d.lim.MaxDepth))
	}
	contentStart := p
	var hardEnd = len(d.b)
	if !indef {
		hardEnd = p + int(contentLen)
		if hardEnd > len(d.b) {
			return p, d.err(KindTruncated, int64(len(d.b)), depth, "constructed content is truncated")
		}
	}

	count := 0
	for {
		if indef {
			if p == len(d.b) {
				return p, d.err(KindTruncated, int64(p), depth, "indefinite value missing EOC terminator")
			}
			if ep, kind := d.leadingEOC(p, true); kind != eocNone {
				if kind == eocTruncated {
					return p, d.err(KindTruncated, int64(len(d.b)), depth, "truncated EOC terminator")
				}
				np := ep + 2
				d.trace("eoc", int64(ep), int64(depth), "matched EOC")
				if err := d.spanCheck(node, contentStart, np-2, depth); err != nil {
					return p, err
				}
				return np, nil
			}
		} else {
			if p >= hardEnd {
				return p, nil
			}
			if ep, kind := d.leadingEOC(p, false); kind == eocPair {
				return p, d.err(KindMalformedEOC, int64(ep), depth,
					"EOC terminates no indefinite-length construction at this position")
			}
		}

		child, np, perr := d.parseValue(p, depth+1)
		if perr != nil {
			return p, perr
		}
		if !indef && int(child.End) > hardEnd {
			return p, d.err(KindInvalidEncoding, child.LengthOffset, depth,
				"child content extends past the enclosing definite-length value")
		}
		count++
		if count > d.lim.MaxChildren {
			return p, d.err(KindSizeExceeded, child.Start, depth,
				fmt.Sprintf("more than %d children in one value", d.lim.MaxChildren))
		}
		node.Children = append(node.Children, child)
		d.trace("child", child.Start, int64(depth), fmt.Sprintf("tag=%d ends=%d", child.Tag, child.End))
		p = np
		if err := d.spanCheck(node, contentStart, p, depth); err != nil {
			return p, err
		}
	}
}

func (d *decoder) spanCheck(node *Node, contentStart, p, depth int) *DecodeError {
	if p-contentStart > d.lim.MaxContentBytes {
		return d.err(KindSizeExceeded, node.Start, depth,
			fmt.Sprintf("constructed content span %d exceeds bound %d", p-contentStart, d.lim.MaxContentBytes))
	}
	return nil
}
