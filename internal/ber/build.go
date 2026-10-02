package ber

import (
	"encoding/hex"
	"math/big"
	"strings"
)

// Spec is the JSON description of a value to encode, accepted by the
// service's encode endpoint.
type Spec struct {
	// Type: "integer", "bitstring", "sequence", "context".
	Type string `json:"type"`
	// Value: decimal integer (type integer).
	Value string `json:"value,omitempty"`
	// Hex: BIT STRING payload or raw primitive content, hex encoded.
	Hex string `json:"hex,omitempty"`
	// UnusedBits: BIT STRING padding count 0..7.
	UnusedBits int `json:"unused_bits,omitempty"`
	// Tag: tag number for type "context".
	Tag uint64 `json:"tag,omitempty"`
	// Constructed: for type "context"/"raw", selects constructed form.
	Constructed bool `json:"constructed,omitempty"`
	// Children: child specs for constructed types.
	Children []Spec `json:"children,omitempty"`
}

// Build converts a Spec into a node tree ready for EncodeBER/EncodeDER.
func Build(s Spec, lim Limits) (*Node, *Error) {
	lim = lim.WithDefaults()
	switch strings.ToLower(s.Type) {
	case "integer":
		v, ok := new(big.Int).SetString(s.Value, 10)
		if !ok {
			return nil, errf(CatSyntax, 0, "invalid integer value %q", s.Value)
		}
		body := encodeTwoComplement(v)
		if len(body) > lim.MaxIntegerBytes {
			return nil, errf(CatResource, 0,
				"INTEGER content of %d octets exceeds limit %d", len(body), lim.MaxIntegerBytes)
		}
		return &Node{Class: Universal, Tag: TagInteger, Content: body,
			ContentLen: len(body), MinimalLen: true}, nil

	case "bitstring":
		if s.UnusedBits < 0 || s.UnusedBits > 7 {
			return nil, errf(CatSyntax, 0,
				"unused-bits count %d out of range 0..7", s.UnusedBits)
		}
		payload, err := hex.DecodeString(s.Hex)
		if err != nil {
			return nil, errf(CatSyntax, 0, "invalid payload hex: %v", err)
		}
		if len(payload) == 0 && s.UnusedBits != 0 {
			return nil, errf(CatSyntax, 0,
				"empty BIT STRING cannot declare unused bits")
		}
		if len(payload) > lim.MaxBitStringBytes {
			return nil, errf(CatResource, 0,
				"BIT STRING payload of %d octets exceeds limit %d",
				len(payload), lim.MaxBitStringBytes)
		}
		content := append([]byte{byte(s.UnusedBits)}, payload...)
		return &Node{Class: Universal, Tag: TagBitString, Content: content,
			ContentLen: len(content), MinimalLen: true}, nil

	case "sequence":
		return buildConstructed(Universal, TagSequence, s.Children, lim)

	case "context":
		if s.Constructed || len(s.Children) > 0 {
			return buildConstructed(Context, s.Tag, s.Children, lim)
		}
		content, err := hex.DecodeString(s.Hex)
		if err != nil {
			return nil, errf(CatSyntax, 0, "invalid content hex: %v", err)
		}
		return &Node{Class: Context, Tag: s.Tag, Content: content,
			ContentLen: len(content), MinimalLen: true}, nil

	}
	return nil, errf(CatSyntax, 0, "unknown spec type %q", s.Type)
}

func buildConstructed(class Class, tag uint64, children []Spec, lim Limits) (*Node, *Error) {
	n := &Node{Class: class, Constructed: true, Tag: tag, MinimalLen: true}
	for i, cs := range children {
		child, err := Build(cs, lim)
		if err != nil {
			return nil, errf(err.Category, err.Offset,
				"child %d: %s", i, err.Msg)
		}
		n.Children = append(n.Children, child)
	}
	return n, nil
}
