package ber

import "math/big"

// Integer decodes a universal INTEGER node as two's-complement (X.690 8.3).
// The content size is bounded by lim.MaxIntegerBytes.
func (n *Node) Integer(lim Limits) (*big.Int, *Error) {
	lim = lim.WithDefaults()
	if !n.Is(Universal, TagInteger) || n.Constructed {
		return nil, errf(CatSyntax, n.Offset, "not a primitive universal INTEGER")
	}
	c := n.Content
	if len(c) == 0 {
		return nil, errf(CatSyntax, n.Offset+n.HeaderLen,
			"INTEGER with empty content")
	}
	if len(c) > lim.MaxIntegerBytes {
		return nil, errf(CatResource, n.Offset+n.HeaderLen,
			"INTEGER content of %d octets exceeds limit %d", len(c), lim.MaxIntegerBytes)
	}
	v := new(big.Int).SetBytes(c)
	if c[0]&0x80 != 0 {
		// Negative: subtract 2^(8*len).
		v.Sub(v, new(big.Int).Lsh(big.NewInt(1), uint(8*len(c))))
	}
	return v, nil
}

// IntegerMinimal reports whether the INTEGER content uses the minimal
// two's-complement encoding required by DER (X.690 8.3.2).
func (n *Node) IntegerMinimal() bool {
	c := n.Content
	if len(c) < 2 {
		return true
	}
	if c[0] == 0x00 && c[1]&0x80 == 0 {
		return false // redundant leading zero
	}
	if c[0] == 0xff && c[1]&0x80 != 0 {
		return false // redundant leading 0xff
	}
	return true
}

// BitString is a decoded BIT STRING body.
type BitString struct {
	// Unused is the number of unused bits in the final octet (0..7).
	Unused int `json:"unused_bits"`
	// Bytes holds the payload octets, including any padding bits.
	Bytes []byte `json:"bytes"`
}

// BitLength returns the number of significant bits.
func (b BitString) BitLength() int {
	return 8*len(b.Bytes) - b.Unused
}

// BitString decodes a universal primitive BIT STRING node (X.690 8.6).
//
// Checks enforced:
//   - content must contain the initial unused-bits octet;
//   - the unused-bits count must be in 0..7;
//   - an empty bit string (no payload octets) must declare 0 unused bits;
//   - the payload size is bounded by lim.MaxBitStringBytes.
func (n *Node) BitString(lim Limits) (BitString, *Error) {
	lim = lim.WithDefaults()
	if !n.Is(Universal, TagBitString) {
		return BitString{}, errf(CatSyntax, n.Offset, "not a universal BIT STRING")
	}
	if n.Constructed {
		return BitString{}, errf(CatSyntax, n.Offset,
			"constructed BIT STRING is not supported at value level")
	}
	c := n.Content
	if len(c) == 0 {
		return BitString{}, errf(CatSyntax, n.Offset+n.HeaderLen,
			"BIT STRING missing unused-bits octet")
	}
	unused := int(c[0])
	if unused > 7 {
		return BitString{}, errf(CatSyntax, n.Offset+n.HeaderLen,
			"unused-bits count %d out of range 0..7", unused)
	}
	payload := c[1:]
	if len(payload) == 0 && unused != 0 {
		return BitString{}, errf(CatSyntax, n.Offset+n.HeaderLen,
			"empty BIT STRING declares %d unused bits", unused)
	}
	if len(payload) > lim.MaxBitStringBytes {
		return BitString{}, errf(CatResource, n.Offset+n.HeaderLen,
			"BIT STRING payload of %d octets exceeds limit %d",
			len(payload), lim.MaxBitStringBytes)
	}
	return BitString{Unused: unused, Bytes: payload}, nil
}

// UnusedBitsZero reports whether the padding bits in the final payload octet
// are all zero, as DER requires (X.690 10.2.1).
func (b BitString) UnusedBitsZero() bool {
	if b.Unused == 0 || len(b.Bytes) == 0 {
		return true
	}
	mask := byte(1<<uint(b.Unused)) - 1
	return b.Bytes[len(b.Bytes)-1]&mask == 0
}

// Validate walks the tree applying value-level rules for the universal types
// this service understands: INTEGER size limits and BIT STRING unused-bits
// checks. Context-specific values are opaque and pass through.
func Validate(n *Node, lim Limits) *Error {
	lim = lim.WithDefaults()
	if n.Class == Universal && !n.Constructed {
		switch n.Tag {
		case TagInteger:
			if _, err := n.Integer(lim); err != nil {
				return err
			}
		case TagBitString:
			if _, err := n.BitString(lim); err != nil {
				return err
			}
		}
	}
	for _, c := range n.Children {
		if err := Validate(c, lim); err != nil {
			return err
		}
	}
	return nil
}
