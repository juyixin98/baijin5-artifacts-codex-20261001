package codec

// Huffman coding per RFC 7541 Appendix B. The decoder is a binary trie
// built once from the normative code table; encoding walks the table
// directly and pads with one-bits (a prefix of EOS) to the byte boundary.

// huffNode is one node of the decoding trie. A child slot holds either the
// index of the next node (>= 0) or a leaf marker: huffLeafBit OR'd with the
// decoded symbol (0..256).
type huffNode struct {
	child [2]int32
}

const huffLeafBit = int32(-1) << 31

var huffTrie []huffNode

func init() {
	huffTrie = append(huffTrie, huffNode{}) // root at index 0
	for sym := 0; sym < 256; sym++ {
		insertHuffCode(uint64(huffmanCodes[sym]), uint(huffmanCodeLen[sym]), int32(sym))
	}
	// EOS (symbol 256) is 30 one-bits; it must be present in the trie so
	// that a payload containing it can be rejected with ErrHuffmanEOS.
	insertHuffCode(0x3fffffff, 30, eosSymbol)
}

func insertHuffCode(code uint64, length uint, sym int32) {
	node := int32(0)
	for i := length; i > 0; i-- {
		bit := (code >> (i - 1)) & 1
		if i == 1 {
			huffTrie[node].child[bit] = huffLeafBit | sym
			return
		}
		next := huffTrie[node].child[bit]
		if next == 0 {
			next = int32(len(huffTrie))
			huffTrie = append(huffTrie, huffNode{})
			huffTrie[node].child[bit] = next
		}
		node = next
	}
}

// AppendHuffman encodes s using the Appendix B code table, padding the
// final byte with one-bits. It returns dst extended with the encoding.
func AppendHuffman(dst []byte, s string) []byte {
	var acc uint64
	var nbits uint
	for i := 0; i < len(s); i++ {
		c := s[i]
		acc = acc<<uint(huffmanCodeLen[c]) | uint64(huffmanCodes[c])
		nbits += uint(huffmanCodeLen[c])
		for nbits >= 8 {
			nbits -= 8
			dst = append(dst, byte(acc>>nbits))
		}
	}
	if nbits > 0 {
		// Pad with a prefix of EOS: all one-bits.
		dst = append(dst, byte(acc<<(8-nbits))|byte(0xff>>nbits))
	}
	return dst
}

// HuffmanEncodedLen returns the encoded length of s in bytes.
func HuffmanEncodedLen(s string) int {
	var nbits uint
	for i := 0; i < len(s); i++ {
		nbits += uint(huffmanCodeLen[s[i]])
	}
	return int((nbits + 7) / 8)
}

// DecodeHuffman decodes a Huffman-coded byte slice into a string.
//
// Failures: ErrHuffmanEOS if the EOS symbol appears in the payload;
// ErrHuffmanPadding if more than 7 bits remain at the end or the remaining
// bits are not all ones (not a prefix of EOS). Empty input decodes to the
// empty string.
func DecodeHuffman(buf []byte) (string, error) {
	out := make([]byte, 0, len(buf)*8/5)
	node := int32(0)
	pending := uint(0) // bits consumed since the last completed symbol
	for _, b := range buf {
		for i := 7; i >= 0; i-- {
			bit := (b >> uint(i)) & 1
			next := huffTrie[node].child[bit]
			if next < 0 {
				sym := int32(uint32(next) & 0x1ff)
				if sym == eosSymbol {
					return "", ErrHuffmanEOS
				}
				out = append(out, byte(sym))
				node = 0
				pending = 0
				continue
			}
			node = next
			pending++
		}
	}
	if pending > 7 {
		return "", ErrHuffmanPadding
	}
	if pending > 0 {
		// The trailing bits are valid padding only if they are all ones
		// (a prefix of EOS). The decoder consumed them starting at the
		// root, so the current node must equal the node reached by
		// following exactly `pending` one-bits from the root.
		probe := int32(0)
		for i := uint(0); i < pending; i++ {
			probe = huffTrie[probe].child[1]
			if probe < 0 {
				return "", ErrHuffmanPadding
			}
		}
		if probe != node {
			return "", ErrHuffmanPadding
		}
	}
	return string(out), nil
}
