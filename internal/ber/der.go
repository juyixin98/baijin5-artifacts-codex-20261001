package ber

import (
	"bytes"
	"math/big"
)

// EncodeDER produces the canonical DER form of a node tree (X.690 clause 10
// and the type-specific clauses 8.3, 8.6, 8.9). Constraints applied:
//
//   - definite, shortest-form lengths everywhere (indefinite input is
//     normalised to definite);
//   - INTEGER content is re-encoded as minimal two's complement;
//   - BIT STRING padding bits are zeroed; constructed BIT STRING is rejected;
//   - SEQUENCE must be constructed; EOC nodes are rejected;
//   - identifier octets use the minimal (short or long) form.
func EncodeDER(n *Node, lim Limits) ([]byte, *Error) {
	lim = lim.WithDefaults()
	return encodeDER(nil, n, lim)
}

func encodeDER(dst []byte, n *Node, lim Limits) ([]byte, *Error) {
	if n.Class == Universal {
		switch n.Tag {
		case TagEOC:
			return nil, errf(CatConstraint, n.Offset,
				"EOC cannot appear in DER output")
		case TagInteger:
			if n.Constructed {
				return nil, errf(CatConstraint, n.Offset,
					"constructed INTEGER is not permitted in DER")
			}
			v, err := n.Integer(lim)
			if err != nil {
				return nil, err
			}
			body := encodeTwoComplement(v)
			dst = appendTag(dst, Universal, false, TagInteger)
			dst = appendLength(dst, len(body))
			return append(dst, body...), nil
		case TagBitString:
			if n.Constructed {
				return nil, errf(CatConstraint, n.Offset,
					"constructed BIT STRING is not permitted in DER")
			}
			bs, err := n.BitString(lim)
			if err != nil {
				return nil, err
			}
			payload := bytes.Clone(bs.Bytes)
			if bs.Unused > 0 && len(payload) > 0 {
				payload[len(payload)-1] &^= byte(1<<uint(bs.Unused)) - 1
			}
			dst = appendTag(dst, Universal, false, TagBitString)
			dst = appendLength(dst, 1+len(payload))
			dst = append(dst, byte(bs.Unused))
			return append(dst, payload...), nil
		case TagSequence:
			if !n.Constructed {
				return nil, errf(CatConstraint, n.Offset,
					"SEQUENCE must use constructed encoding in DER")
			}
		}
	}

	dst = appendTag(dst, n.Class, n.Constructed, n.Tag)
	if !n.Constructed {
		dst = appendLength(dst, len(n.Content))
		return append(dst, n.Content...), nil
	}
	var body []byte
	var err *Error
	for _, c := range n.Children {
		body, err = encodeDER(body, c, lim)
		if err != nil {
			return nil, err
		}
	}
	dst = appendLength(dst, len(body))
	return append(dst, body...), nil
}

// encodeTwoComplement returns the minimal two's-complement octets of v, as
// required for DER INTEGER content (X.690 8.3.2).
func encodeTwoComplement(v *big.Int) []byte {
	if v.Sign() >= 0 {
		b := v.Bytes()
		if len(b) == 0 {
			return []byte{0x00}
		}
		if b[0]&0x80 != 0 {
			return append([]byte{0x00}, b...)
		}
		return b
	}
	// Negative: find the smallest n such that -2^(8n-1) <= v.
	n := 1
	for {
		lo := new(big.Int).Lsh(big.NewInt(-1), uint(8*n-1))
		if v.Cmp(lo) >= 0 {
			break
		}
		n++
	}
	mod := new(big.Int).Lsh(big.NewInt(1), uint(8*n))
	u := new(big.Int).Add(v, mod) // 0 <= u < 2^(8n)
	b := u.Bytes()
	out := make([]byte, n)
	copy(out[n-len(b):], b)
	return out
}

// VerifyDER checks that data is exactly one canonically encoded DER value.
// It parses with indefinite lengths disabled, re-encodes canonically, and
// compares octet for octet; the reported offset is the first differing octet.
func VerifyDER(data []byte, lim Limits) *Error {
	lim = lim.WithDefaults().DisableIndefinite()
	root, err := DecodeAll(data, lim)
	if err != nil {
		if err.Category == CatIndefinite {
			return errf(CatConstraint, err.Offset,
				"indefinite-length form is not DER")
		}
		return err
	}
	canon, cerr := EncodeDER(root, lim)
	if cerr != nil {
		return cerr
	}
	if !bytes.Equal(canon, data) {
		off := firstDiff(data, canon)
		return errf(CatConstraint, off,
			"encoding is not canonical DER (input and canonical form differ here)")
	}
	return nil
}

func firstDiff(a, b []byte) int {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			return i
		}
	}
	return n
}
