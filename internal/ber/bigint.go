package ber

import (
	"fmt"
	"math/big"
)

// BigIntegerNode builds an INTEGER node from an arbitrary-precision
// integer, emitting minimal two's-complement content. The width is still
// bounded by Limits.MaxIntegerBytes on validation.
func BigIntegerNode(v *big.Int) *Node {
	return &Node{Class: ClassUniversal, Tag: TagInteger, Value: bigIntToTwos(v)}
}

// BigIntegerValue decodes an INTEGER node into an arbitrary-precision int.
func BigIntegerValue(n *Node) (*big.Int, error) {
	if !n.IsUniversal(TagInteger) {
		return nil, fmt.Errorf("not an INTEGER")
	}
	if len(n.Value) == 0 {
		return nil, fmt.Errorf("empty INTEGER")
	}
	v := new(big.Int).SetBytes(n.Value) // unsigned magnitude
	if n.Value[0]&0x80 != 0 {
		// negative: v -= 2^(8*len)
		mod := new(big.Int).Lsh(big.NewInt(1), uint(8*len(n.Value)))
		v.Sub(v, mod)
	}
	return v, nil
}

func bigIntToTwos(v *big.Int) []byte {
	if v.Sign() == 0 {
		return []byte{0}
	}
	b := v.Bytes() // minimal positive big-endian magnitude
	if v.Sign() > 0 {
		if b[0]&0x80 != 0 {
			return append([]byte{0x00}, b...)
		}
		return b
	}
	// negative: smallest w with |v| <= 2^(8w-1), then tc = 2^(8w) - |v|.
	mag := new(big.Int).Neg(v)
	w := 1
	half := new(big.Int).Lsh(big.NewInt(1), uint(7)) // 2^7
	for mag.Cmp(half) > 0 {
		w++
		half.Lsh(half, 8)
	}
	mod := new(big.Int).Lsh(big.NewInt(1), uint(8*w))
	tc := new(big.Int).Add(v, mod) // v negative, so mod - |v|
	out := tc.Bytes()
	if len(out) < w {
		out = append(make([]byte, w-len(out)), out...)
	}
	return out
}
