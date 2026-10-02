package codec

// huffmanCodeLen holds the canonical Huffman code length, in bits, for each
// input byte (symbols 0..255). These lengths are the normative values from
// RFC 7541 Appendix B; the actual codewords are derived at init time by the
// canonical prefix-code algorithm rather than hard-coded. Symbol 256 (EOS,
// 30 ones) is deliberately absent: it never occurs inside a header string
// and is used only to validate trailing padding.
var huffmanCodeLen = [256]uint8{
	13, 23, 28, 28, 28, 28, 28, 28,
	28, 24, 30, 28, 28, 30, 28, 28,
	28, 28, 28, 28, 28, 28, 30, 28,
	28, 28, 28, 28, 28, 28, 28, 28,
	6, 10, 10, 12, 13, 6, 8, 11,
	10, 10, 8, 11, 8, 6, 6, 6,
	5, 5, 5, 6, 6, 6, 6, 6,
	6, 6, 7, 8, 15, 6, 12, 10,
	13, 6, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7,
	8, 7, 8, 13, 19, 13, 14, 6,
	15, 5, 6, 5, 6, 5, 6, 6,
	6, 5, 7, 7, 6, 6, 6, 5,
	6, 7, 6, 5, 5, 6, 7, 7,
	7, 7, 7, 15, 11, 14, 13, 28,
	20, 22, 20, 20, 22, 22, 22, 23,
	22, 23, 23, 23, 23, 23, 24, 23,
	24, 24, 22, 23, 24, 23, 23, 23,
	23, 21, 22, 23, 22, 23, 23, 24,
	22, 21, 20, 22, 22, 23, 23, 21,
	23, 22, 22, 24, 21, 22, 23, 23,
	21, 21, 22, 21, 23, 22, 23, 23,
	20, 22, 22, 22, 23, 22, 22, 23,
	26, 26, 20, 19, 22, 23, 22, 25,
	26, 26, 26, 27, 27, 26, 24, 25,
	19, 21, 26, 27, 27, 26, 27, 24,
	21, 21, 26, 26, 28, 27, 27, 27,
	20, 24, 20, 21, 22, 21, 21, 23,
	22, 22, 25, 25, 24, 24, 26, 23,
	26, 27, 26, 26, 27, 27, 27, 27,
	27, 28, 27, 27, 27, 27, 27, 26,
}

// eosBits is the length of the end-of-string code (symbol 256): thirty 1 bits.
const eosBits = 30

// huffNode is a binary prefix-tree node.
type huffNode struct {
	children [2]*huffNode
	sym      byte
	leaf     bool
	depth    uint8
	// onesSpine marks nodes reachable from the root using only 1 bits, i.e.
	// prefixes of the 30-one EOS code. Trailing padding is legal only while
	// the decoder stands on such a node (RFC 7541 section 5.2).
	onesSpine bool
}

var (
	huffRoot      *huffNode
	huffCodes     [256]uint32
	maxHuffCodeLn uint8
)

func init() {
	buildHuffmanCodes()
	huffRoot = buildHuffmanTree()
}

// buildHuffmanCodes derives canonical codewords from code lengths. Symbols
// must be visited in ascending (length, symbol) order: the first code of each
// length is the previous code plus one, shifted left by the length
// difference. The RFC 7541 lengths are not monotonic in symbol order, so the
// sort is load-bearing.
func buildHuffmanCodes() {
	type symLen struct {
		sym int
		ln  uint8
	}
	ordered := make([]symLen, 0, 256)
	for sym := 0; sym < 256; sym++ {
		if huffmanCodeLen[sym] != 0 {
			ordered = append(ordered, symLen{sym, huffmanCodeLen[sym]})
			if huffmanCodeLen[sym] > maxHuffCodeLn {
				maxHuffCodeLn = huffmanCodeLen[sym]
			}
		}
	}
	for i := 1; i < len(ordered); i++ {
		for j := i; j > 0; j-- {
			a, b := ordered[j-1], ordered[j]
			if b.ln < a.ln || (b.ln == a.ln && b.sym < a.sym) {
				ordered[j-1], ordered[j] = b, a
				continue
			}
			break
		}
	}
	var code uint32
	var prevLen uint8
	for _, e := range ordered {
		code <<= e.ln - prevLen
		huffCodes[e.sym] = code
		code++
		prevLen = e.ln
	}
}

func buildHuffmanTree() *huffNode {
	root := &huffNode{onesSpine: true}
	for sym := 0; sym < 256; sym++ {
		ln := uint(huffmanCodeLen[sym])
		if ln == 0 {
			continue
		}
		cur := root
		for bit := ln - 1; ; bit-- {
			side := byte(huffCodes[sym]>>bit) & 1
			next := cur.children[side]
			if next == nil {
				next = &huffNode{depth: cur.depth + 1}
				cur.children[side] = next
			}
			cur = next
			if bit == 0 {
				break
			}
		}
		cur.leaf = true
		cur.sym = byte(sym)
	}
	// Mark the EOS spine (thirty 1 bits from the root).
	cur := root
	for i := 0; i < eosBits; i++ {
		cur = cur.children[1]
		if cur == nil {
			break
		}
		cur.onesSpine = true
	}
	return root
}

// HuffmanEncodedLen reports the wire length (rounded up to the next octet) of
// the Huffman encoding of s.
func HuffmanEncodedLen(s []byte) int {
	var bits uint64
	for _, b := range s {
		bits += uint64(huffmanCodeLen[b])
	}
	return int((bits + 7) / 8)
}

// AppendHuffman encodes s and appends it to dst. Trailing bits up to the
// octet boundary are padded with the most-significant bits of the EOS code
// (all ones), per RFC 7541 section 5.2.
func AppendHuffman(dst, s []byte) []byte {
	var acc uint64
	var nbits uint
	for _, b := range s {
		ln := uint(huffmanCodeLen[b])
		nbits += ln
		acc = acc<<ln | uint64(huffCodes[b])
		for nbits >= 8 {
			nbits -= 8
			dst = append(dst, byte(acc>>nbits))
			acc &= uint64(1)<<nbits - 1
		}
	}
	if nbits > 0 {
		pad := 8 - nbits
		dst = append(dst, byte(acc<<pad)|byte(0xff>>nbits))
	}
	return dst
}

// DecodeHuffman decodes a Huffman-coded string.
//
// maxLen > 0 bounds the number of decompressed bytes; longer output yields
// ErrStringLengthLimit. The function enforces every rule of RFC 7541
// section 5.2: unknown prefixes are rejected, an incomplete symbol is legal
// only as a <=7-bit all-ones prefix of EOS padding, and padding that is not
// all ones is rejected.
func DecodeHuffman(src []byte, maxLen int) ([]byte, error) {
	if len(src) == 0 {
		return []byte{}, nil
	}
	// Shortest code is 5 bits, so output can be at most ceil(8/5)x input.
	out := make([]byte, 0, len(src)+(len(src)+2)/2)
	node := huffRoot
	for _, b := range src {
		for bit := uint(0); bit < 8; bit++ {
			side := b >> (7 - bit) & 1
			node = node.children[side]
			if node == nil {
				return nil, ErrInvalidHuffman
			}
			if node.leaf {
				if maxLen > 0 && len(out) >= maxLen {
					return nil, ErrStringLengthLimit
				}
				out = append(out, node.sym)
				node = huffRoot
			}
		}
	}
	if node == huffRoot {
		// Stream ended exactly on an octet boundary.
		return out, nil
	}
	// Between one and seven bits remain. They are legal padding only when
	// they form a strict prefix of EOS (the all-ones spine); an incomplete
	// symbol or an all-ones string longer than seven bits is rejected.
	if node.depth <= 7 && node.onesSpine {
		return out, nil
	}
	return nil, ErrInvalidHuffman
}
