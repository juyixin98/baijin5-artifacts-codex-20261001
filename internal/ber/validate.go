package ber

import (
	"bytes"
	"fmt"
)

// ValidateBER enforces the restricted-profile content rules on a decoded
// tree: INTEGER width and two's-complement form, BIT STRING unused-bits
// rules, constructed/primitive consistency for SEQUENCE/SET, and the
// resource limits that apply to content rather than framing.
func (n *Node) ValidateBER(lim Limits) error {
	if e := n.validate(lim, false); e != nil {
		return e
	}
	return nil
}

// ValidateDER enforces every BER rule plus the independent DER canonical
// constraints (X.690 clause 10/11): definite minimal lengths, minimal
// long-form tags, definite-length constructions, primitive BIT STRING and
// OCTET STRING, BOOLEAN 0x00/0xFF, minimal INTEGER, SET OF ordering.
func (n *Node) ValidateDER(lim Limits) error {
	if e := n.validate(lim, true); e != nil {
		return e
	}
	return nil
}

func (n *Node) validate(lim Limits, der bool) *DecodeError {
	lim = lim.normalized()
	return validateRec(n, lim, der, 0)
}

func verr(kind ErrorKind, n *Node, off int64, depth int, format string, args ...any) *DecodeError {
	return &DecodeError{Kind: kind, Offset: off, Depth: depth, Msg: fmt.Sprintf(format, args...)}
}

func validateRec(n *Node, lim Limits, der bool, depth int) *DecodeError {
	// Tag-length canonical rules (DER only).
	if der {
		if e := checkCanonicalTag(n, depth); e != nil {
			return e
		}
		if e := checkCanonicalLength(n, depth); e != nil {
			return e
		}
	}

	if n.Class != ClassUniversal {
		// Application/private tags are outside the restricted profile;
		// context-specific tags are supported.
		if n.Class != ClassContext {
			return verr(KindUnsupported, n, n.Start, depth,
				"tag class %s is outside the restricted profile", n.Class)
		}
		if n.Constructed {
			return validateChildren(n, lim, der, depth)
		}
		return nil // opaque context primitive
	}

	switch n.Tag {
	case TagInteger:
		return validateInteger(n, lim, der, depth)
	case TagBitString:
		return validateBitString(n, lim, der, depth)
	case TagSequence:
		if !n.Constructed {
			return verr(KindInvalidEncoding, n, n.Start, depth, "SEQUENCE must be constructed")
		}
		return validateChildren(n, lim, der, depth)
	case TagSet:
		if !n.Constructed {
			return verr(KindInvalidEncoding, n, n.Start, depth, "SET must be constructed")
		}
		if e := validateChildren(n, lim, der, depth); e != nil {
			return e
		}
		if der {
			return checkSetOrdering(n, depth)
		}
		return nil
	case TagBoolean:
		if n.Constructed {
			return verr(KindInvalidEncoding, n, n.Start, depth, "BOOLEAN must be primitive")
		}
		if len(n.Value) != 1 {
			return verr(KindInvalidEncoding, n, n.HeaderEnd, depth, "BOOLEAN content must be exactly one octet")
		}
		if der && n.Value[0] != 0x00 && n.Value[0] != 0xFF {
			return verr(KindInvalidEncoding, n, n.HeaderEnd, depth,
				"DER BOOLEAN must be 0x00 or 0xFF, got 0x%02X", n.Value[0])
		}
		return nil
	case TagNull:
		if n.Constructed {
			return verr(KindInvalidEncoding, n, n.Start, depth, "NULL must be primitive")
		}
		if len(n.Value) != 0 {
			return verr(KindInvalidEncoding, n, n.HeaderEnd, depth, "NULL content must be empty")
		}
		return nil
	case TagOctetString:
		if n.Constructed {
			if der {
				return verr(KindInvalidEncoding, n, n.Start, depth, "DER OCTET STRING must use primitive encoding")
			}
			return validateChildren(n, lim, der, depth)
		}
		return nil
	default:
		return verr(KindUnsupported, n, n.Start, depth,
			"universal tag %d is outside the restricted profile", n.Tag)
	}
}

func validateChildren(n *Node, lim Limits, der bool, depth int) *DecodeError {
	for _, c := range n.Children {
		if e := validateRec(c, lim, der, depth+1); e != nil {
			return e
		}
	}
	return nil
}

func validateInteger(n *Node, lim Limits, der bool, depth int) *DecodeError {
	if n.Constructed {
		return verr(KindInvalidEncoding, n, n.Start, depth, "INTEGER must be primitive")
	}
	if len(n.Value) == 0 {
		return verr(KindInvalidInteger, n, n.HeaderEnd, depth, "INTEGER content must not be empty")
	}
	if len(n.Value) > lim.MaxIntegerBytes {
		return verr(KindSizeExceeded, n, n.HeaderEnd, depth,
			"INTEGER width %d exceeds bound %d", len(n.Value), lim.MaxIntegerBytes)
	}
	if der && len(n.Value) > 1 {
		// X.690 8.3.2: the encoding is non-minimal exactly when the first
		// octet and the sign bit of the second octet are all zeros
		// (0x00 followed by a clear sign bit) or all ones (0xFF followed by
		// a set sign bit). Other leading octets (01 00 = 256, FE FF = -257)
		// are already minimal.
		first, second := n.Value[0], n.Value[1]
		allZero := first == 0x00 && second&0x80 == 0
		allOne := first == 0xFF && second&0x80 != 0
		if allZero || allOne {
			return verr(KindInvalidEncoding, n, n.HeaderEnd, depth,
				"DER INTEGER is not in minimal two's-complement form")
		}
	}
	return nil
}

func validateBitString(n *Node, lim Limits, der bool, depth int) *DecodeError {
	if n.Constructed {
		if der {
			return verr(KindInvalidEncoding, n, n.Start, depth, "DER BIT STRING must use primitive encoding")
		}
		// BER constructed form: children are concatenated segments. Every
		// segment except the last must be a complete-octet (0 unused bits)
		// primitive BIT STRING; the last carries the real unused-bits count.
		if len(n.Children) == 0 {
			return verr(KindInvalidBitString, n, n.HeaderEnd, depth,
				"constructed BIT STRING contains no segments")
		}
		for i, seg := range n.Children {
			if !seg.IsUniversal(TagBitString) || seg.Constructed {
				return verr(KindInvalidBitString, seg, seg.Start, depth,
					"constructed BIT STRING segment %d is not a primitive BIT STRING", i)
			}
			last := i == len(n.Children)-1
			if e := checkBitStringPayload(seg, lim, depth, !last); e != nil {
				return e
			}
		}
		return nil
	}
	return checkBitStringPayload(n, lim, depth, false)
}

// checkBitStringPayload validates one primitive BIT STRING content.
// When completeOctets is true (a non-final constructed segment) the
// unused-bits octet must be zero.
func checkBitStringPayload(n *Node, lim Limits, depth int, completeOctets bool) *DecodeError {
	if len(n.Value) == 0 {
		return verr(KindInvalidBitString, n, n.HeaderEnd, depth,
			"BIT STRING content must start with an unused-bits octet")
	}
	unused := n.Value[0]
	off := n.HeaderEnd
	if unused > 7 {
		return verr(KindInvalidBitString, n, off, depth,
			"unused-bits count %d is outside 0..7", unused)
	}
	if completeOctets && unused != 0 {
		return verr(KindInvalidBitString, n, off, depth,
			"non-final BIT STRING segment must have 0 unused bits, got %d", unused)
	}
	if len(n.Value) == 1 {
		if unused != 0 {
			return verr(KindInvalidBitString, n, off, depth,
				"empty BIT STRING must have 0 unused bits, got %d", unused)
		}
		return nil
	}
	if int(unused) > (len(n.Value)-1)*8 {
		return verr(KindInvalidBitString, n, off, depth,
			"unused-bits count %d exceeds the %d payload bits", unused, (len(n.Value)-1)*8)
	}
	last := n.Value[len(n.Value)-1]
	if unused > 0 && last&(0xFF>>(8-unused)) != 0 {
		return verr(KindInvalidBitString, n, off+int64(len(n.Value)-1), depth,
			"the %d unused low bits of the final octet must be zero (0x%02X)", unused, last)
	}
	return nil
}

func checkCanonicalTag(n *Node, depth int) *DecodeError {
	raw := n.rawTag
	// Long form: initial octet plus continuation octets; non-minimal when
	// the first continuation octet carries no value bits (0x00 final or
	// 0x80 continuing): the tag fit in fewer base-128 digits.
	if len(raw) >= 2 && raw[1]&0x7F == 0 {
		return verr(KindInvalidEncoding, n, n.Start, depth,
			"DER tag number is not in minimal base-128 form")
	}
	return nil
}

func checkCanonicalLength(n *Node, depth int) *DecodeError {
	if n.Indefinite {
		return verr(KindInvalidEncoding, n, n.LengthOffset, depth,
			"DER requires definite-length encoding")
	}
	raw := n.rawLength
	if len(raw) >= 2 {
		cnt := raw[0] & 0x7F
		// With 2+ value octets the encoding is non-minimal only if the
		// most significant octet is 0x00 (the value fit in fewer octets).
		if cnt > 1 && raw[1] == 0x00 {
			return verr(KindInvalidEncoding, n, n.LengthOffset, depth,
				"DER length is not in minimal form")
		}
		// A single value octet below 0x80 must have used the short form.
		if cnt == 1 && raw[1] < 0x80 {
			return verr(KindInvalidEncoding, n, n.LengthOffset, depth,
				"DER length %d must use the short form", raw[1])
		}
	}
	return nil
}

// checkSetOrdering enforces the DER SET OF order: elements sorted by
// their encoded octets (X.690 11.6). Children have already passed DER
// validation at this point, so re-encoding them is canonical.
func checkSetOrdering(n *Node, depth int) *DecodeError {
	var prev []byte
	for i, c := range n.Children {
		cur, encErr := EncodeChecked(c, DER)
		if encErr != nil {
			return verr(KindInvalidEncoding, c, c.Start, depth,
				"SET element cannot be DER-encoded: %s", encErr.Error())
		}
		if i > 0 && bytes.Compare(prev, cur) > 0 {
			return verr(KindInvalidEncoding, c, c.Start, depth,
				"DER SET/SET OF elements are not in ascending encoded order")
		}
		prev = cur
	}
	return nil
}
