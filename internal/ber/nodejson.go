package ber

import "encoding/hex"

// NodeJSON is the wire representation of a decoded value returned by the
// service API. Typed universal values carry decoded fields; everything else
// carries raw content hex.
type NodeJSON struct {
	Class       string     `json:"class"`
	Tag         uint64     `json:"tag"`
	Type        string     `json:"type,omitempty"`
	Constructed bool       `json:"constructed"`
	Offset      int        `json:"offset"`
	HeaderLen   int        `json:"header_len"`
	Length      int        `json:"length"`
	Indefinite  bool       `json:"indefinite,omitempty"`
	Value       string     `json:"value,omitempty"` // INTEGER, decimal
	UnusedBits  *int       `json:"unused_bits,omitempty"`
	BitLength   int        `json:"bit_length,omitempty"`
	Hex         string     `json:"hex,omitempty"` // primitive content (or BIT STRING payload)
	Children    []NodeJSON `json:"children,omitempty"`
}

// universalTypeName names the universal tags this service decodes natively.
func universalTypeName(tag uint64) string {
	switch tag {
	case TagInteger:
		return "INTEGER"
	case TagBitString:
		return "BIT STRING"
	case TagSequence:
		return "SEQUENCE"
	}
	return ""
}

// ToJSON converts a node tree to its wire representation. Value-level
// decoding errors for universal types are surfaced in the Hex fallback and
// must not occur after a successful Validate.
func (n *Node) ToJSON(lim Limits) NodeJSON {
	j := NodeJSON{
		Class:       n.Class.String(),
		Tag:         n.Tag,
		Constructed: n.Constructed,
		Offset:      n.Offset,
		HeaderLen:   n.HeaderLen,
		Length:      n.ContentLen,
		Indefinite:  n.Indefinite,
	}
	if n.Class == Universal {
		j.Type = universalTypeName(n.Tag)
	}
	if n.Constructed {
		for _, c := range n.Children {
			j.Children = append(j.Children, c.ToJSON(lim))
		}
		return j
	}
	switch {
	case n.Is(Universal, TagInteger):
		if v, err := n.Integer(lim); err == nil {
			j.Value = v.String()
			return j
		}
	case n.Is(Universal, TagBitString):
		if bs, err := n.BitString(lim); err == nil {
			u := bs.Unused
			j.UnusedBits = &u
			j.BitLength = bs.BitLength()
			j.Hex = hex.EncodeToString(bs.Bytes)
			return j
		}
	}
	j.Hex = hex.EncodeToString(n.Content)
	return j
}
